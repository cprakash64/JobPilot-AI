/**
 * Field normalization + mapping.
 *
 * Deterministic hierarchy: sensitive detection → file/upload → autocomplete
 * attribute → name/id rules → label/nearby text. An LLM is never given DOM
 * control; it could only classify, and only after this deterministic pass fails.
 *
 * Confidence policy:
 *   >= 0.95  auto-fill verified facts
 *   0.80–0.94 fill but mark for review (non-sensitive)
 *   < 0.80   do not auto-fill (ask the user)
 *   sensitive: never auto-fill unless explicitly verified + enabled
 */

import type { ApplicationSessionData, DiscoveredField, FieldMapping, FieldMappingResult } from "../types";
import { detectSensitive } from "./sensitive";
import type { CanonicalField, MappingSource } from "./taxonomy";
import { CUSTOM_RESPONSE_FIELDS, UPLOAD_FIELDS } from "./taxonomy";

export const AUTO_FILL_THRESHOLD = 0.95;
export const REVIEW_THRESHOLD = 0.8;

type Classification = {
  canonicalKey: CanonicalField;
  confidence: number;
  source: MappingSource;
  sensitive: boolean;
  explanation: string;
};

const AUTOCOMPLETE_MAP: Record<string, CanonicalField> = {
  email: "email",
  tel: "phone",
  "tel-national": "phone",
  "given-name": "first_name",
  "additional-name": "middle_name",
  "family-name": "last_name",
  name: "full_name",
  "street-address": "address",
  "address-line1": "address",
  "address-level2": "city",
  "address-level1": "state",
  "postal-code": "postal_code",
  country: "country",
  "country-name": "country",
  organization: "current_company",
  "organization-title": "current_title"
};

// Ordered keyword rules over the combined field text (name + id + label + …).
const KEYWORD_RULES: { key: CanonicalField; pattern: RegExp; confidence: number }[] = [
  { key: "first_name", pattern: /\b(first name|given name|forename|fname)\b/i, confidence: 0.93 },
  // Middle name/initial must be recognized separately, or it inherits the
  // first/last rules and the whole split shifts by one token.
  { key: "middle_name", pattern: /\b(middle name|middle initial|middle_name|mname|\bmi\b)\b/i, confidence: 0.93 },
  { key: "last_name", pattern: /\b(last name|surname|family name|lname)\b/i, confidence: 0.93 },
  { key: "full_name", pattern: /\b(full name|your name|legal name)\b/i, confidence: 0.9 },
  // More specific preferred first/last must win over the generic preferred_name
  // (classifyField keeps the highest-confidence match).
  { key: "preferred_first_name", pattern: /\bpreferred first name\b/i, confidence: 0.94 },
  { key: "preferred_last_name", pattern: /\bpreferred (?:last|family) name\b/i, confidence: 0.94 },
  { key: "preferred_name", pattern: /\b(preferred name|name you go by)\b/i, confidence: 0.92 },
  { key: "pronouns", pattern: /\bpronouns?\b/i, confidence: 0.93 },
  { key: "email", pattern: /\b(e-?mail)\b/i, confidence: 0.95 },
  // A phone COUNTRY selector must outrank the generic phone rule, or "Phone
  // country code" is treated as the number field and receives "+16028161309".
  {
    key: "phone_country",
    pattern: /\b(country code|phone country|dial(?:ing)? code|calling code|country\s*\/\s*region code)\b/i,
    confidence: 0.94
  },
  { key: "phone", pattern: /\b(phone|mobile|telephone|cell)\b/i, confidence: 0.92 },
  { key: "linkedin_url", pattern: /linkedin/i, confidence: 0.96 },
  { key: "github_url", pattern: /github/i, confidence: 0.96 },
  { key: "portfolio_url", pattern: /\b(portfolio|personal (?:web)?site|website|personal url)\b/i, confidence: 0.85 },
  { key: "postal_code", pattern: /\b(zip|postal code|postcode)\b/i, confidence: 0.9 },
  { key: "city", pattern: /\b(city|town)\b/i, confidence: 0.85 },
  { key: "state", pattern: /\b(state|province|region)\b/i, confidence: 0.82 },
  { key: "country", pattern: /\bcountry\b/i, confidence: 0.9 },
  { key: "address", pattern: /\b(street address|address line|mailing address|\baddress\b)\b/i, confidence: 0.85 },
  { key: "current_company", pattern: /\b(current (?:employer|company)|company name)\b/i, confidence: 0.85 },
  { key: "current_title", pattern: /\b(current (?:title|role|position)|job title)\b/i, confidence: 0.83 },
  { key: "salary_expectation", pattern: /\b(salary expectation|desired (?:salary|compensation)|expected (?:salary|pay))\b/i, confidence: 0.85 },
  { key: "available_start_date", pattern: /\b(start date|available (?:start|to start)|availability|earliest start)\b/i, confidence: 0.83 },
  { key: "years_of_experience", pattern: /\byears? (?:of )?experience\b/i, confidence: 0.85 },
  { key: "willing_to_relocate", pattern: /\brelocat/i, confidence: 0.82 },
  { key: "preferred_workplace", pattern: /\b(remote|on-?site|hybrid|work location preference|workplace preference)\b/i, confidence: 0.7 },
  // Sponsorship "now" vs "future" are logically different questions and are
  // NEVER collapsed into one answer (see canonical.py VERIFICATION_REQUIRED_KEYS
  // — both stay fill-then-flag regardless, but must map to the right key).
  // Order here doesn't affect precedence (classifyField keeps the
  // highest-confidence match), so the "future" rule (mentions "future"
  // explicitly) is simply given no chance to lose to the "now" rule on the
  // same text by never overlapping on the deciding keyword.
  {
    key: "sponsorship_required_future",
    pattern: /(?:now or (?:in )?the future|will require|will you.*require).*sponsorship|sponsorship.*(?:in the future|future sponsorship)/i,
    confidence: 0.86
  },
  {
    key: "sponsorship_required_now",
    pattern: /\b(?:currently require|currently need|do you (?:currently )?(?:require|need))\b.*sponsorship|\brequire sponsorship\b|\bneed sponsorship\b|\bvisa sponsorship\b/i,
    // Deliberately just above work_authorization_us: a label can legitimately
    // contain both "sponsorship" and "work authorization" wording (e.g. "…to
    // maintain work authorization"), and when it explicitly asks about
    // sponsorship, that more specific question must win — never collapsed
    // into the general work-authorization answer.
    confidence: 0.855
  },
  { key: "work_authorization_us", pattern: /\b(authori[sz]ed to work|work authori[sz]ation|legally authori[sz]ed|right to work)\b/i, confidence: 0.85 },
  { key: "referral_source", pattern: /\b(how did you (?:hear|find out)|where did you hear|referral source|how did you learn)\b/i, confidence: 0.87 },
  { key: "previously_employed", pattern: /\b(previously (?:worked|(?:been )?employed)|former(?:ly)? employee|worked (?:here|at this company) before)\b/i, confidence: 0.87 },
  { key: "relatives_employed", pattern: /\b(relatives?|family member).{0,20}(?:work|employed)|(?:work|employed).{0,20}relatives?\b/i, confidence: 0.83 },
  { key: "previously_interviewed", pattern: /\b(previously interviewed|interviewed (?:here|with us) before|prior interview)\b/i, confidence: 0.83 },
  { key: "custom_motivation", pattern: /\b(why (?:do you )?want|why (?:are you )?interested|why (?:this|our) (?:role|company)|what interests you)\b/i, confidence: 0.8 },
  { key: "custom_experience", pattern: /\b(describe (?:your )?(?:experience|a (?:relevant )?project)|tell us about|relevant experience)\b/i, confidence: 0.78 }
];

/** Classify a single field into a canonical key (session-independent). */
export function classifyField(field: DiscoveredField): Classification {
  const text = combinedText(field);
  const primaryText = [field.name, field.id, field.ariaLabel, field.label, field.placeholder]
    .filter(Boolean)
    .join(" ");

  // Greenhouse labels its phone-prefix dropdown simply "Country". Identify it
  // from the small shared row that also contains the Phone control; a standalone
  // application-country field elsewhere on the form is left as `country`.
  if (isPhoneCountryCompanion(field)) {
    return {
      canonicalKey: "phone_country", confidence: 0.98, source: "deterministic", sensitive: false,
      explanation: "Country selector paired with the adjacent phone-number control."
    };
  }

  // Exact high-consequence questions must be classified from the control's own
  // accessible name, before nearby/help text can introduce overlapping words.
  // Greenhouse's sponsorship prompt itself contains "work authorization", so a
  // generic combined-text pass alone can otherwise map the adjacent controls to
  // the same key.
  if (/\bare you (?:currently )?legally authori[sz]ed to work\b|\blegally authori[sz]ed to work in (?:the )?country\b/i.test(primaryText)) {
    return {
      canonicalKey: "work_authorization_us", confidence: 0.98, source: "label", sensitive: false,
      explanation: "Exact legal work-authorization question."
    };
  }
  if (/\bwill you now or (?:in )?the future require\b.*\bsponsorship\b|\brequire\b.*\bsponsorship\b.*\b(?:now or )?(?:in )?the future\b/i.test(primaryText)) {
    return {
      canonicalKey: "sponsorship_required_future", confidence: 0.98, source: "label", sensitive: false,
      explanation: "Exact current-or-future sponsorship question."
    };
  }
  if (/\blocation\s*\(\s*city\s*\)|^\s*city\s*\*?\s*$/i.test(field.label || field.ariaLabel || field.placeholder)) {
    return {
      canonicalKey: "city", confidence: 0.97, source: "label", sensitive: false,
      explanation: "Exact city/location field."
    };
  }

  // A candidate/data-privacy acknowledgement is a narrow consent the user has
  // explicitly asked JobPilot to accept while applying. Recognize it before
  // the generic `acknowledge` legal-attestation detector, but do not broaden
  // this to employment, conflict, originality, or AI-use attestations.
  if (
    /\b(?:candidate|applicant|data)?\s*privacy\s+(?:policy|notice|statement)\b/i.test(text) &&
    !/\b(?:artificial intelligence|\bai\b|original work|non-?compete|non-?solicitation|previously employed|worked for)\b/i.test(text)
  ) {
    return {
      canonicalKey: "privacy_policy_acknowledgement",
      confidence: 0.99,
      source: "deterministic",
      sensitive: false,
      explanation: "Candidate/data privacy policy acknowledgement with a narrowly controlled affirmative option."
    };
  }

  // 1. Sensitive categories always win. The mapping policy below only permits
  // auto-fill when the backend has included an explicitly verified + enabled
  // vault answer for this exact canonical key.
  const sensitive = detectSensitive(text);
  if (sensitive.sensitive && sensitive.key) {
    return { canonicalKey: sensitive.key, confidence: 0.9, source: "deterministic", sensitive: true,
      explanation: `Detected sensitive category "${sensitive.category}" — requires an explicitly verified answer.` };
  }

  // 2. File inputs → resume / cover letter.
  if (field.control === "file") {
    if (/cover.?letter/i.test(text)) {
      return { canonicalKey: "cover_letter_upload", confidence: 0.95, source: "deterministic", sensitive: false,
        explanation: "File input labeled cover letter." };
    }
    if (/resume|résumé|\bcv\b|curriculum vitae/i.test(text)) {
      return { canonicalKey: "resume_upload", confidence: 0.95, source: "deterministic", sensitive: false,
        explanation: "File input labeled resume/CV." };
    }
    return { canonicalKey: "unknown", confidence: 0, source: "deterministic", sensitive: false,
      explanation: "Unlabeled file input — attach manually." };
  }

  // 3. autocomplete attribute (most reliable when present).
  const auto = AUTOCOMPLETE_MAP[field.autocomplete];
  if (auto) {
    return { canonicalKey: auto, confidence: 0.97, source: "autocomplete", sensitive: false,
      explanation: `HTML autocomplete="${field.autocomplete}".` };
  }
  if (field.inputType === "email") {
    return { canonicalKey: "email", confidence: 0.96, source: "deterministic", sensitive: false,
      explanation: "input[type=email]." };
  }

  // 4/5. name/id + label keyword rules (first, highest-confidence match wins).
  let best: Classification | null = null;
  for (const rule of KEYWORD_RULES) {
    if (rule.pattern.test(text) && (!best || rule.confidence > best.confidence)) {
      best = { canonicalKey: rule.key, confidence: rule.confidence, source: "label", sensitive: false,
        explanation: `Matched "${rule.pattern.source}".` };
    }
  }
  if (best) {
    return best;
  }

  return { canonicalKey: "unknown", confidence: 0, source: "deterministic", sensitive: false,
    explanation: "No confident match — needs your input." };
}

/** Map all fields, applying the confidence + session-availability policy. */
export function buildMappings(fields: DiscoveredField[], session: ApplicationSessionData): FieldMappingResult {
  const answers = new Map(session.answers.map((a) => [a.canonical_key, a]));
  const mappings: FieldMapping[] = [];
  const unmapped: string[] = [];

  for (const field of fields) {
    const c = classifyField(field);
    if (c.canonicalKey === "unknown") {
      unmapped.push(field.uid);
      // A required choice control may still be safely attempted with the
      // user-approved singleton-affirmation sentinel. The adapter will select
      // only when the employer exposes exactly one substantive I agree / I
      // acknowledge / Yes option; otherwise it closes the menu and reviews it.
      mappings.push(mapping(field, c, {
        safeToAutoFill: isRequiredChoice(field),
        requiresReview: true
      }));
      continue;
    }
    if (c.sensitive) {
      const answer = answers.get(c.canonicalKey);
      const explicitlyAllowed = Boolean(
        answer?.value && answer.sensitive && answer.verified && !answer.requires_review
      );
      mappings.push(mapping(field, c, {
        safeToAutoFill: explicitlyAllowed || isRequiredChoice(field),
        requiresReview: !explicitlyAllowed
      }));
      continue;
    }
    if (UPLOAD_FIELDS.has(c.canonicalKey)) {
      mappings.push(mapping(field, c, { safeToAutoFill: true, requiresReview: false }));
      continue;
    }
    if (c.canonicalKey === "privacy_policy_acknowledgement") {
      // The dropdown adapter still verifies that the control exposes exactly
      // one substantive acknowledgement option before selecting it.
      mappings.push(mapping(field, c, { safeToAutoFill: true, requiresReview: false }));
      continue;
    }
    if (CUSTOM_RESPONSE_FIELDS.has(c.canonicalKey)) {
      // Written responses are suggested (AI-drafted) and always reviewed.
      mappings.push(mapping(field, c, { safeToAutoFill: false, requiresReview: true }));
      continue;
    }
    const answer = answers.get(c.canonicalKey);
    if (!answer || !answer.value) {
      if (c.canonicalKey === "referral_source") {
        // User-approved default. A saved company-scoped answer (including a
        // referral) always wins above; this sentinel only matches an employer
        // careers/company-website option and never falls back to another item.
        mappings.push(mapping(field, c, { safeToAutoFill: true, requiresReview: false }));
        continue;
      }
      if (isRequiredChoice(field)) {
        mappings.push(mapping(field, c, { safeToAutoFill: true, requiresReview: true }));
        continue;
      }
      // Mapped but we have no verified value — user must supply it.
      mappings.push(mapping(field, c, { safeToAutoFill: false, requiresReview: true }));
      continue;
    }
    if (c.confidence >= AUTO_FILL_THRESHOLD && !answer.requires_review) {
      mappings.push(mapping(field, c, { safeToAutoFill: true, requiresReview: false }));
    } else if (c.confidence >= REVIEW_THRESHOLD) {
      mappings.push(mapping(field, c, { safeToAutoFill: true, requiresReview: true }));
    } else {
      mappings.push(mapping(field, c, { safeToAutoFill: false, requiresReview: true }));
    }
  }
  return { mappings, unmapped };
}

function isRequiredChoice(field: DiscoveredField): boolean {
  return field.required && ["select", "combobox", "listbox", "radio"].includes(field.control);
}

function isPhoneCountryCompanion(field: DiscoveredField): boolean {
  if (!/^\s*country\s*\*?\s*$/i.test(field.label || field.ariaLabel || field.placeholder)) return false;
  const el = field.element;
  if (!el) return false;
  let node: HTMLElement | null = el.parentElement;
  for (let depth = 0; node && depth < 5 && node.tagName.toLowerCase() !== "form"; depth += 1) {
    const controls = node.querySelectorAll('input:not([type="hidden"]),select,textarea,[role="combobox"]');
    // Stop before a broad form section can make an unrelated Country field look
    // phone-adjacent. The actual Greenhouse phone row has 2–3 controls (the
    // React-Select search input may be counted alongside its wrapper).
    if (controls.length > 6) return false;
    const ownText = (node.textContent || "").replace(/\s+/g, " ");
    const hasPhoneControl = Array.from(controls).some((control) => {
      if (control === el || el.contains(control)) return false;
      const input = control as HTMLInputElement;
      const identity = [input.name, input.id, input.getAttribute("aria-label"), input.getAttribute("type")]
        .filter(Boolean)
        .join(" ");
      return /\b(phone|mobile|telephone|tel)\b/i.test(identity);
    });
    if (hasPhoneControl || (/\bphone\b/i.test(ownText) && controls.length >= 2)) return true;
    node = node.parentElement;
  }
  return false;
}

function mapping(field: DiscoveredField, c: Classification, flags: { safeToAutoFill: boolean; requiresReview: boolean }): FieldMapping {
  return {
    uid: field.uid,
    canonicalKey: c.canonicalKey,
    confidence: c.confidence,
    mappingSource: c.source,
    safeToAutoFill: flags.safeToAutoFill,
    requiresReview: flags.requiresReview,
    sensitive: c.sensitive,
    explanation: c.explanation
  };
}

function combinedText(field: DiscoveredField): string {
  return [field.name, field.id, field.ariaLabel, field.label, field.placeholder, field.nearbyText, field.sectionHeading]
    .filter(Boolean)
    .join(" ");
}
