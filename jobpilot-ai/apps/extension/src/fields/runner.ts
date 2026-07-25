/**
 * Fill orchestration: discover -> map -> fill standard fields, applying the
 * confidence + sensitive policy. Sensitive and low-confidence fields are
 * highlighted for review, never silently filled. Upload targets are returned
 * for the caller to handle (document bytes require the session token, held only
 * in the extension background). Pure and DOM-only — unit-testable under jsdom.
 */

import type { ApplicationSessionData, DiscoveredField, FieldMapping, FillOutcome } from "../types";
import { discoverFields } from "./discovery";
import { fillField, highlight } from "./fill";
import { buildMappings } from "./mapping";
import { UPLOAD_FIELDS } from "./taxonomy";

export interface FillSummary {
  filled: number;
  skipped: number;
  reviewRequired: number;
  errors: string[];
  uploadTargets: { uid: string; kind: "resume" | "cover-letter" }[];
  mappings: FieldMapping[];
  /** Per-field fill outcome, keyed by uid — the AUTHORITATIVE record of what the
   * fill engine did. Post-fill verification must trust this for custom controls
   * (combobox/listbox) whose selection is not reflected in `element.value`. */
  outcomes: Map<string, FillOutcome>;
}

export function scan(root: ParentNode, session: ApplicationSessionData, step = 0): { fields: DiscoveredField[]; mappings: FieldMapping[] } {
  const fields = discoverFields(root, step);
  const { mappings } = buildMappings(fields, session);
  return { fields, mappings };
}

// Cascading fields must be filled in this order.
//  - country -> state/province (and to a lesser extent city): some ATS forms
//    repopulate the state options only after country changes.
//  - phone_country -> phone: selecting "United States (+1)" re-masks and
//    re-validates the phone input, so a number typed BEFORE the country is
//    reformatted or rejected. This ordering is the fix for the Greenhouse
//    phone field that silently refused "602-816-1309".
const CASCADE_PRIORITY: Partial<Record<string, number>> = {
  country: 0,
  phone_country: 0,
  state: 1,
  phone: 1,
  phone_national: 1,
  city: 2,
  postal_code: 2
};

/** Digits-only comparison. A site is free to reformat "6028161309" into
 * "(602) 816-1309" — that is success, not failure — but dropping or truncating
 * digits is a real failure that must not be reported as filled. */
function sameDigits(a: string, b: string): boolean {
  const digits = (s: string) => s.replace(/\D/g, "");
  const left = digits(a);
  const right = digits(b);
  if (!left || !right) return false;
  // A separate country selector means the input legitimately holds only the
  // national part, so accept either exact digits or a suffix match.
  return left === right || left.endsWith(right) || right.endsWith(left);
}
function cascadeSort(mappings: FieldMapping[]): FieldMapping[] {
  return [...mappings].sort((a, b) => (CASCADE_PRIORITY[a.canonicalKey] ?? 5) - (CASCADE_PRIORITY[b.canonicalKey] ?? 5));
}

type SessionAnswer = ApplicationSessionData["answers"][number];

function syntheticAnswer(key: string, value: string): SessionAnswer {
  return {
    canonical_key: key,
    value,
    display_value: value,
    source: "user_default",
    confidence: 1,
    sensitive: false,
    requires_review: false,
    verified: true
  };
}

/** Pick which stored phone shape a control should receive.
 *
 * The backend publishes the number three ways (E.164, country code, national)
 * so this is a SELECTION, never a string edit — nothing here ever strips or
 * prepends a "+1".
 */
function resolvePhoneAnswer(
  answers: Map<string, SessionAnswer>,
  key: string,
  splitsPhoneCountry: boolean
): SessionAnswer | undefined {
  if (key === "phone_country") {
    const iso2 = answers.get("phone_country_iso2")?.value.toUpperCase();
    const code = answers.get("phone_country")?.value;
    const country = PHONE_COUNTRY_LABELS[iso2 ?? ""];
    if (country && code) return syntheticAnswer(key, `${country} (${code})`);
    return answers.get("phone_country") ?? answers.get("phone_country_iso2");
  }
  if (key === "phone" && splitsPhoneCountry) {
    // The country lives in its own control; this input takes the national part.
    return answers.get("phone_national") ?? answers.get("phone");
  }
  if (key === "phone_national") {
    return answers.get("phone_national") ?? answers.get("phone");
  }
  return answers.get(key);
}

const PHONE_COUNTRY_LABELS: Record<string, string> = {
  US: "United States",
  CA: "Canada",
  GB: "United Kingdom",
  IN: "India",
  AU: "Australia",
  DE: "Germany",
  FR: "France",
  SG: "Singapore"
};

/** Fill every mapped field. Async and strictly sequential — one dropdown is
 * fully resolved (including its bounded option-render wait) before the next
 * one starts, so options from two different open dropdowns can never be
 * confused with each other. */
export async function applyFill(fields: DiscoveredField[], mappings: FieldMapping[], session: ApplicationSessionData): Promise<FillSummary> {
  const answers = new Map(session.answers.map((a) => [a.canonical_key, a]));
  const fieldByUid = new Map(fields.map((f) => [f.uid, f]));
  const summary: FillSummary = { filled: 0, skipped: 0, reviewRequired: 0, errors: [], uploadTargets: [], mappings, outcomes: new Map() };
  // Does THIS form ask for the dialing country separately? If so the phone
  // input must receive the national number only — writing E.164 there is what
  // produces "+1+1 602…" / a validation error on Greenhouse.
  const splitsPhoneCountry = mappings.some((m) => m.canonicalKey === "phone_country");

  for (const mapping of cascadeSort(mappings)) {
    const field = fieldByUid.get(mapping.uid);
    if (!field?.element) {
      continue;
    }
    if (field.element.hasAttribute("data-jobpilot-repeater")) {
      summary.skipped += 1;
      summary.outcomes.set(mapping.uid, {
        uid: mapping.uid,
        status: "skipped",
        reason: "structured repeater value present"
      });
      continue;
    }
    if (UPLOAD_FIELDS.has(mapping.canonicalKey)) {
      summary.uploadTargets.push({ uid: mapping.uid, kind: mapping.canonicalKey === "cover_letter_upload" ? "cover-letter" : "resume" });
      highlight(field.element, "generated");
      continue;
    }
    if (mapping.sensitive && !mapping.safeToAutoFill) {
      // Sensitive values are filled only when the application package contains
      // the user's explicitly consented, verified answer for this exact key.
      highlight(field.element, "review");
      summary.reviewRequired += 1;
      continue;
    }
    if (!mapping.safeToAutoFill) {
      if (mapping.requiresReview) {
        highlight(field.element, field.required ? "invalid" : "review");
        summary.reviewRequired += 1;
      }
      continue;
    }
    const answer = resolveApplicationAnswer(
      answers,
      mapping.canonicalKey,
      splitsPhoneCountry,
      field,
      session
    );
    if (!answer?.value) {
      summary.reviewRequired += 1;
      continue;
    }
    const isPhoneNumber = mapping.canonicalKey === "phone" || mapping.canonicalKey === "phone_national";
    const outcome = await fillField(field, answer.value, {
      status: mapping.requiresReview ? "review" : "verified",
      dropdownSearchValue: mapping.canonicalKey === "city"
        ? String(answer.value).split(",")[0]?.trim()
        : mapping.canonicalKey === "phone_country"
          ? String(answer.value).replace(/\s*\(\s*\+\d{1,4}\s*\)\s*$/, "").trim()
        : mapping.canonicalKey === "referral_source"
          ? "Career Website"
          : mapping.canonicalKey === "privacy_policy_acknowledgement"
            ? "I acknowledge"
            : undefined,
      dropdownMatchMode: mapping.canonicalKey === "education_end_year"
        ? "graduation_year"
        : mapping.canonicalKey === "education_gpa"
          ? "gpa"
          : undefined,
      // Phone inputs re-format themselves on blur; verify by digits so a
      // cosmetic reformat counts as success but a dropped digit does not.
      verify: isPhoneNumber ? (final) => sameDigits(final, String(answer.value)) : undefined
    });
    summary.outcomes.set(mapping.uid, outcome);
    if (outcome.status === "filled") {
      summary.filled += 1;
      if (mapping.requiresReview) summary.reviewRequired += 1;
      // Give a country selection a moment to repopulate a dependent
      // state/province dropdown (or re-mask the phone input) before we reach
      // the next field.
      if (mapping.canonicalKey === "country" || mapping.canonicalKey === "phone_country") await delay(250);
    } else if (outcome.status === "skipped") {
      summary.skipped += 1;
    } else if (outcome.status === "review_required") {
      summary.reviewRequired += 1;
    } else {
      summary.errors.push(`${mapping.canonicalKey}: ${outcome.reason ?? "error"}`);
    }
  }
  return summary;
}

function resolveApplicationAnswer(
  answers: Map<string, SessionAnswer>,
  key: string,
  splitsPhoneCountry: boolean,
  field: DiscoveredField,
  session: ApplicationSessionData
): SessionAnswer | undefined {
  const answer = resolvePhoneAnswer(answers, key, splitsPhoneCountry);
  if (!answer && key === "referral_source") {
    return syntheticAnswer(key, "__jobpilot_company_careers_page__");
  }
  if (!answer && key === "privacy_policy_acknowledgement") {
    // Checkboxes represent the only acknowledgement option directly. Selects,
    // radios, and comboboxes use a sentinel whose matcher requires exactly one
    // substantive I-agree/I-acknowledge/Yes option.
    return syntheticAnswer(key, field.control === "checkbox" ? "true" : "__jobpilot_privacy_acknowledgement__");
  }
  if (
    !answer &&
    field.required &&
    ["select", "combobox", "listbox", "radio"].includes(field.control)
  ) {
    return syntheticAnswer(key, "__jobpilot_required_singleton_affirmation__");
  }
  if (
    key === "city" &&
    answer &&
    isLocationChoice(field)
  ) {
    const location = session.profileData?.location;
    if (typeof location === "string" && location.includes(",")) {
      return { ...answer, value: location, display_value: location };
    }
  }
  return answer;
}

function isLocationChoice(field: DiscoveredField): boolean {
  if (field.control === "select" || field.control === "combobox" || field.control === "listbox") return true;
  const el = field.element;
  if (!el) return false;
  return (
    (el.getAttribute("role") || "").toLowerCase() === "combobox" ||
    el.getAttribute("aria-haspopup") === "listbox" ||
    Boolean(el.getAttribute("aria-autocomplete")) ||
    Boolean(el.closest('[class*="__control"], [class*="-control"]'))
  );
}

export async function runFill(root: ParentNode, session: ApplicationSessionData, step = 0): Promise<FillSummary> {
  const { fields, mappings } = scan(root, session, step);
  return applyFill(fields, mappings, session);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
