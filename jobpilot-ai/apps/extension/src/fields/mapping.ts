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
  { key: "last_name", pattern: /\b(last name|surname|family name|lname)\b/i, confidence: 0.93 },
  { key: "full_name", pattern: /\b(full name|your name|legal name)\b/i, confidence: 0.9 },
  { key: "email", pattern: /\b(e-?mail)\b/i, confidence: 0.95 },
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
  { key: "sponsorship_required_now", pattern: /\b(require sponsorship|need sponsorship|visa sponsorship)\b/i, confidence: 0.85 },
  { key: "work_authorization_us", pattern: /\b(authori[sz]ed to work|work authori[sz]ation|legally authori[sz]ed|right to work)\b/i, confidence: 0.85 },
  { key: "custom_motivation", pattern: /\b(why (?:do you )?want|why (?:are you )?interested|why (?:this|our) (?:role|company)|what interests you)\b/i, confidence: 0.8 },
  { key: "custom_experience", pattern: /\b(describe (?:your )?(?:experience|a (?:relevant )?project)|tell us about|relevant experience)\b/i, confidence: 0.78 }
];

/** Classify a single field into a canonical key (session-independent). */
export function classifyField(field: DiscoveredField): Classification {
  const text = combinedText(field);

  // 1. Sensitive categories always win and are never auto-filled.
  const sensitive = detectSensitive(text);
  if (sensitive.sensitive && sensitive.key) {
    return { canonicalKey: sensitive.key, confidence: 0.9, source: "deterministic", sensitive: true,
      explanation: `Detected sensitive category "${sensitive.category}" — left for you to answer.` };
  }

  // 2. File inputs → resume / cover letter.
  if (field.control === "file") {
    if (/cover.?letter/i.test(text)) {
      return { canonicalKey: "cover_letter_upload", confidence: 0.95, source: "deterministic", sensitive: false,
        explanation: "File input labeled cover letter." };
    }
    return { canonicalKey: "resume_upload", confidence: 0.9, source: "deterministic", sensitive: false,
      explanation: "File input (resume/CV)." };
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
      mappings.push(mapping(field, c, { safeToAutoFill: false, requiresReview: true }));
      continue;
    }
    if (c.sensitive) {
      mappings.push(mapping(field, c, { safeToAutoFill: false, requiresReview: true }));
      continue;
    }
    if (UPLOAD_FIELDS.has(c.canonicalKey)) {
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
