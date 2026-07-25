/**
 * Pure derivation of the review list from the durable field ledger. Both the
 * content script and the tests use this single function, so what the widget
 * shows can never disagree with the ledger the counts come from.
 */

import { UNRESOLVED_STATUSES, type LedgerEntry } from "../fields/ledger";
import type { ApplicationSessionData } from "../types";
import type { ReviewCategory, ReviewItem } from "./widget";

const REASON_TEXT: Partial<Record<string, string>> = {
  NO_VERIFIED_ANSWER: "JobPilot doesn't have a confirmed answer for this question.",
  LOW_CONFIDENCE: "JobPilot found this field but isn't confident enough to fill it automatically.",
  SENSITIVE_FIELD: "This is a sensitive/voluntary question — please choose an answer yourself.",
  VALUE_DID_NOT_STICK: "JobPilot tried to fill this field, but the page didn't accept the value.",
  DOCUMENT_DOWNLOAD_FAILED: "Could not download the generated document.",
  DOCUMENT_UPLOAD_REJECTED: "The employer page rejected the automatic upload.",
  ADAPTER_NOT_DETECTED: "JobPilot can't operate this type of control yet.",
  UNSUPPORTED_CONTROL: "JobPilot can't operate this type of control yet.",
  // Dropdown-specific: say exactly what happened, and always offer the real options.
  DROPDOWN_NOT_VISIBLE: "This dropdown wasn't visible on the page — scroll to it and choose an answer.",
  DROPDOWN_DISABLED: "This dropdown is disabled on the page right now.",
  DROPDOWN_OPEN_FAILED: "JobPilot couldn't open this dropdown — choose an answer and it will be applied.",
  LISTBOX_NOT_FOUND: "JobPilot opened this dropdown but couldn't find its option list.",
  OPTIONS_NOT_FOUND: "This dropdown didn't render any options — choose an answer to apply.",
  OPTION_NOT_AVAILABLE: "Your saved answer isn't one of this employer's options — pick the closest match.",
  DROPDOWN_SELECTION_FAILED: "JobPilot couldn't select an option here — choose one and it will be applied.",
  DROPDOWN_VERIFICATION_FAILED: "JobPilot selected an option but the page didn't keep it — please choose it yourself."
};

const CLEAR_QUESTION: Partial<Record<string, string>> = {
  country: "Which country do you currently live in?",
  phone_country: "Which country calling code should be used for your phone number?",
  education_school: "Which school, college, or university did you attend?",
  education_degree: "What type of degree did you earn?",
  education_major: "What was your major or field of study?",
  education_end_year: "In what year did you graduate?",
  education_gpa: "What was your GPA?",
  city: "Which city do you currently live in?",
  state: "Which state or province do you currently live in?",
  postal_code: "What is your current ZIP or postal code?"
};

const CLEAR_HELP: Partial<Record<string, string>> = {
  country: "Choose the employer's country option that matches your saved location.",
  phone_country: "Choose the calling-code option for your saved phone number (for example, United States +1).",
  education_school: "Enter the institution name exactly as you want it shown on this application.",
  education_degree: "Choose the closest degree level offered by the employer (for example, Master's Degree for a Master of Science).",
  education_major: "Enter the subject you studied, sometimes called your discipline or field of study.",
  education_end_year: "Use the four-digit graduation year for this education entry.",
  education_gpa: "Use the scale requested by the employer; do not convert it unless the form asks you to.",
  city: "Choose your current city from the employer's available options.",
  state: "Choose your current state, province, or region.",
  postal_code: "Enter the postal code for your current address."
};

function manualAttachmentReason(e: LedgerEntry): string | null {
  if (e.canonicalKey === "undergraduate_transcript_upload") {
    return "Use the employer's Attach button to upload the undergraduate transcript requested for this application.";
  }
  if (e.canonicalKey === "graduate_transcript_upload") {
    return "Use the employer's Attach button to upload your graduate transcript. Skip it when you do not have a graduate degree and the field is optional.";
  }
  return null;
}

export function categoryForEntry(e: LedgerEntry): ReviewCategory {
  if (e.sensitive) return "sensitive";
  if (e.status === "technical_failure" || e.status === "unsupported_control") return "technical";
  if (!e.reusable) return "application";
  return e.required ? "required" : "optional";
}

export function reasonForEntry(e: LedgerEntry): string {
  const attachmentReason = manualAttachmentReason(e);
  if (attachmentReason) return attachmentReason;
  if (e.canonicalKey && CLEAR_HELP[e.canonicalKey]) return CLEAR_HELP[e.canonicalKey] as string;
  if (e.reasonCode && REASON_TEXT[e.reasonCode]) return REASON_TEXT[e.reasonCode] as string;
  switch (e.status) {
    case "needs_confirmation":
      return e.sensitive
        ? "This is a sensitive/voluntary question — please choose an answer yourself."
        : "This needs your explicit acknowledgement for this application.";
    case "unsupported_control":
      return "JobPilot can't operate this type of control automatically — choose an answer and it will fill it.";
    case "technical_failure":
      return "JobPilot tried to fill this, but the page didn't accept the value.";
    default:
      return "JobPilot doesn't have a confirmed answer for this question.";
  }
}

export interface ReviewModel {
  items: ReviewItem[];
  /** uid -> canonical key, so a later save knows what it's persisting. */
  keyByUid: Map<string, string>;
  /** uid -> default answer-vault scope. */
  scopeByUid: Map<string, "global" | "company">;
}

/** Build the review list from the ledger's unresolved entries + the structured
 * name-confirmation prompt. Every blank required control is included — mapped or
 * not, sensitive or not, supported or not. */
export function buildReviewModel(entries: LedgerEntry[], session: ApplicationSessionData): ReviewModel {
  const items: ReviewItem[] = [];
  const keyByUid = new Map<string, string>();
  const scopeByUid = new Map<string, "global" | "company">();

  const nameQ = (key: string) =>
    session.unresolvedQuestions.find((q) => q.canonical_key === key && q.action === "confirm_name");
  const firstQ = nameQ("first_name");
  const middleQ = nameQ("middle_name");
  const lastQ = nameQ("last_name");
  if (firstQ || middleQ || lastQ) {
    items.push({
      id: "name_confirm",
      kind: "name_confirm",
      category: "required",
      question: "JobPilot needs you to confirm how your name should be divided.",
      required: true,
      reasonText:
        firstQ?.reason ||
        lastQ?.reason ||
        "Confirm how your name splits into first, middle, and last name.",
      // first|middle|last|preferredFirst|preferredLast — the suggestion only;
      // nothing is applied until the user confirms it.
      suggestedValue: [
        firstQ?.suggested_value ?? "",
        middleQ?.suggested_value ?? "",
        lastQ?.suggested_value ?? "",
        "",
        ""
      ].join("|"),
      reusable: false
    });
  }

  const reasonByKey = new Map(
    session.unresolvedQuestions.filter((q) => q.reason).map((q) => [q.canonical_key, q.reason as string])
  );

  for (const e of entries) {
    if (!UNRESOLVED_STATUSES.has(e.status) || e.verified) continue;
    if (e.uid.startsWith("upload:")) continue; // documents resolve through the ATS "Attach" UI
    keyByUid.set(e.uid, e.canonicalKey ?? "unknown");
    if (e.defaultScope) scopeByUid.set(e.uid, e.defaultScope);
    items.push({
      id: e.uid,
      kind: "field",
      category: categoryForEntry(e),
      question: (e.canonicalKey && CLEAR_QUESTION[e.canonicalKey]) || e.question,
      required: e.required,
      reasonText: reasonByKey.get(e.canonicalKey ?? "") ?? reasonForEntry(e),
      options: e.options.length > 0 ? e.options : undefined,
      control: e.controlType,
      multiple: e.multiple,
      reusable: e.reusable,
      defaultScope: e.defaultScope
    });
  }
  return { items, keyByUid, scopeByUid };
}
