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

export function categoryForEntry(e: LedgerEntry): ReviewCategory {
  if (e.sensitive) return "sensitive";
  if (e.status === "technical_failure" || e.status === "unsupported_control") return "technical";
  if (!e.reusable) return "application";
  return e.required ? "required" : "optional";
}

export function reasonForEntry(e: LedgerEntry): string {
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
      question: e.question,
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
