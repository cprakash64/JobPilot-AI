/**
 * The field ledger — the ONE source of truth for form completeness.
 *
 * Every actionable control discovered in the DOM produces exactly one ledger
 * entry with exactly one terminal status. Widget counts, the review list, and
 * the "can this application be marked complete?" gate are ALL derived from this
 * ledger — there are no independent counters anywhere else. This is what makes
 * "All caught up" impossible while a visible required control is still blank.
 *
 * The ledger is durable across rescans: entries are keyed by the stable
 * DiscoveredField.uid (WeakMap-backed in discovery), so a late/partial SPA
 * re-render MERGES into the ledger instead of replacing (and dropping) it.
 */

import type { FieldFillResult } from "../messages";
import type { DiscoveredField } from "../types";
import { isPlaceholderText } from "./discovery";
import { selectAdapter } from "./dropdown/registry";

export type LedgerStatus =
  | "filled_verified"
  | "filled_needs_review"
  | "missing_information"
  | "needs_confirmation"
  | "intentionally_skipped_optional"
  | "unsupported_control"
  | "technical_failure"
  | "user_entered"
  | "not_applicable";

export interface LedgerEntry {
  uid: string;
  frameId: string;
  label: string;
  normalizedLabel: string;
  controlType: string;
  canonicalKey: string | null;
  required: boolean;
  sensitive: boolean;
  options: string[];
  multiple: boolean;
  currentValuePresent: boolean;
  status: LedgerStatus;
  reasonCode: string;
  fillSource: string | null;
  verified: boolean;
  /** Widget-facing extras (not part of the invariant, but carried on the entry
   * so the review list is derived from the ledger alone). */
  question: string;
  reusable: boolean;
  defaultScope?: "global" | "company";
}

/** Statuses that leave a required control BLANK — the set that gates completion
 * and forbids "All caught up". */
export const UNRESOLVED_STATUSES: ReadonlySet<LedgerStatus> = new Set<LedgerStatus>([
  "missing_information",
  "needs_confirmation",
  "unsupported_control",
  "technical_failure"
]);

const FILLED_STATUSES: ReadonlySet<LedgerStatus> = new Set<LedgerStatus>([
  "filled_verified",
  "filled_needs_review",
  "user_entered"
]);

export interface LedgerCounts {
  discovered: number;
  filled: number;
  needsInformation: number;
  needsConfirmation: number;
  sensitive: number;
  technical: number;
  optionalSkipped: number;
  /** Blank REQUIRED controls — the gate for "Mark complete" / "Ready for review". */
  requiredBlank: number;
  /** Everything the user still has to touch (drives the review toggle label). */
  pending: number;
}

/** Map a per-field fill result onto a terminal ledger status. */
export function statusFromResult(field: DiscoveredField, result: FieldFillResult | undefined): {
  status: LedgerStatus;
  reasonCode: string;
  verified: boolean;
} {
  const reason: string = result?.reasonCode ?? "";
  if (!result) {
    // Discovered but never processed (e.g. reconciliation-only). Decide from the
    // live DOM: a blank required control is missing info; an optional blank is a
    // deliberate optional skip.
    return valuePresent(field)
      ? { status: "filled_verified", reasonCode: "", verified: true }
      : field.required
        ? { status: "missing_information", reasonCode: "NO_VERIFIED_ANSWER", verified: false }
        : { status: "intentionally_skipped_optional", reasonCode: "", verified: false };
  }
  switch (result.status) {
    case "filled":
    case "already_filled":
      return { status: "filled_verified", reasonCode: "", verified: true };
    case "skipped":
      if (reason === "USER_VALUE_PRESENT") return { status: "user_entered", reasonCode: reason, verified: true };
      if (reason === "DISABLED_FIELD") return { status: "not_applicable", reasonCode: reason, verified: false };
      return { status: "intentionally_skipped_optional", reasonCode: reason, verified: false };
    case "not_found":
      return { status: "technical_failure", reasonCode: reason || "VALUE_DID_NOT_STICK", verified: false };
    case "failed":
      return { status: "technical_failure", reasonCode: reason || "VALUE_DID_NOT_STICK", verified: false };
    case "review":
    default:
      return { status: reviewStatus(field, reason), reasonCode: reason || "NO_VERIFIED_ANSWER", verified: false };
  }
}

function reviewStatus(field: DiscoveredField, reason: string): LedgerStatus {
  if (reason === "SENSITIVE_FIELD") return "needs_confirmation";
  if (reason === "UNSUPPORTED_CONTROL" || reason === "ADAPTER_NOT_DETECTED") return "unsupported_control";
  if (reason === "DOCUMENT_DOWNLOAD_FAILED" || reason === "DOCUMENT_UPLOAD_REJECTED" || reason === "VALUE_DID_NOT_STICK") {
    return "technical_failure";
  }
  // A required checkbox is a consent / policy acknowledgement / attestation: it
  // requires the user's explicit confirmation, never a guess — needs_confirmation
  // rather than plain missing info.
  if ((field.control === "checkbox" && field.required) || reason === "REQUIRES_ACKNOWLEDGEMENT") {
    return "needs_confirmation";
  }
  return "missing_information";
}

/** Whether the live control currently holds a real (non-placeholder) value.
 *
 * For every dropdown-like control this delegates to the dropdown adapter's
 * `readSelection`, which knows where each library actually stores its value
 * (React Select chips/singleValue, aria-selected options, a portal listbox, a
 * hidden input). A placeholder such as "Select..." is NEVER a value — that is
 * what let a blank required dropdown be reported "Ready for review". */
export function valuePresent(field: DiscoveredField): boolean {
  const el = field.element as HTMLInputElement | HTMLSelectElement | undefined;
  if (!el) return Boolean(field.existingValue && field.existingValue.trim() && !isPlaceholderText(field.existingValue));
  const input = el as HTMLInputElement;

  const adapter = selectAdapter(field);
  if (adapter) return adapter.readSelection(field).length > 0;

  if (input.type === "checkbox") return input.checked;
  if (input.type === "file") return Boolean(input.files && input.files.length);
  const value = (el.value ?? el.textContent ?? "").trim();
  return Boolean(value && !isPlaceholderText(value));
}

/** Build the ledger from the discovered fields and their fill results. Fields
 * without a result are reconciled from live DOM state (never dropped). */
export function buildLedger(
  fields: DiscoveredField[],
  results: FieldFillResult[],
  meta: (field: DiscoveredField) => { canonicalKey: string | null; sensitive: boolean; reusable: boolean; defaultScope?: "global" | "company"; fillSource: string | null }
): LedgerEntry[] {
  const resultByUid = new Map<string, FieldFillResult>();
  for (const r of results) if (r.uid) resultByUid.set(r.uid, r);

  const entries: LedgerEntry[] = [];
  const seen = new Set<string>();
  for (const field of fields) {
    if (seen.has(field.uid)) continue; // duplicate wrappers collapse to one entry
    seen.add(field.uid);
    const result = resultByUid.get(field.uid);
    const m = meta(field);
    const question = attachmentQuestion(m.canonicalKey)
      || field.label || field.ariaLabel || field.placeholder || field.name || m.canonicalKey || "This question";
    const { status, reasonCode, verified } = statusFromResult(field, result);
    entries.push({
      uid: field.uid,
      frameId: field.frameId,
      label: field.label || field.ariaLabel || field.placeholder || field.name || m.canonicalKey || "",
      normalizedLabel: field.normalizedLabel,
      controlType: field.control,
      canonicalKey: m.canonicalKey,
      required: field.required,
      sensitive: m.sensitive,
      // Prefer the options the dropdown adapter saw in the LIVE menu (a portal
      // menu renders nothing until opened, so discovery-time options are empty).
      options: result?.options?.length ? result.options : field.options,
      multiple: field.multiple,
      currentValuePresent: valuePresent(field),
      status,
      reasonCode,
      fillSource: verified ? m.fillSource : null,
      verified,
      question,
      reusable: m.reusable,
      defaultScope: m.defaultScope
    });
  }
  return entries;
}

function attachmentQuestion(key: string | null): string {
  if (key === "undergraduate_transcript_upload") return "Undergraduate transcript";
  if (key === "graduate_transcript_upload") return "Graduate transcript";
  return "";
}

/** Merge a fresh scan's entries into the durable ledger. A re-render only
 * upgrades an entry (blank → filled, or unsupported → filled), never regresses a
 * verified entry back to blank just because a partial rescan missed it. */
export function mergeLedger(existing: LedgerEntry[], incoming: LedgerEntry[]): LedgerEntry[] {
  const byUid = new Map(existing.map((e) => [e.uid, e]));
  for (const entry of incoming) {
    const prior = byUid.get(entry.uid);
    if (!prior) {
      byUid.set(entry.uid, entry);
      continue;
    }
    // Prefer the entry that represents MORE progress; keep a verified prior if
    // the new scan couldn't confirm the value (element detached mid-rescan).
    if (prior.verified && !entry.verified && !entry.currentValuePresent) continue;
    byUid.set(entry.uid, entry);
  }
  return Array.from(byUid.values());
}

export function computeCounts(entries: LedgerEntry[]): LedgerCounts {
  let filled = 0;
  let needsInformation = 0;
  let needsConfirmation = 0;
  let sensitive = 0;
  let technical = 0;
  let optionalSkipped = 0;
  let requiredBlank = 0;
  let pending = 0;

  for (const e of entries) {
    if (FILLED_STATUSES.has(e.status)) filled += 1;
    if (e.status === "missing_information") needsInformation += 1;
    if (e.status === "needs_confirmation") needsConfirmation += 1;
    if (e.status === "unsupported_control" || e.status === "technical_failure") technical += 1;
    if (e.status === "intentionally_skipped_optional" || e.status === "not_applicable") optionalSkipped += 1;
    const unresolved = UNRESOLVED_STATUSES.has(e.status) && !e.verified;
    if (unresolved) {
      pending += 1;
      if (e.sensitive) sensitive += 1;
      if (e.required) requiredBlank += 1;
    }
  }
  return { discovered: entries.length, filled, needsInformation, needsConfirmation, sensitive, technical, optionalSkipped, requiredBlank, pending };
}

/** Invariant self-check: discovered === sum of every terminal-status bucket.
 * Returned (not thrown) so a diagnostics build can surface a violation without
 * breaking the user's flow. */
export function ledgerInvariantHolds(entries: LedgerEntry[]): boolean {
  const counts: Record<LedgerStatus, number> = {
    filled_verified: 0,
    filled_needs_review: 0,
    missing_information: 0,
    needs_confirmation: 0,
    intentionally_skipped_optional: 0,
    unsupported_control: 0,
    technical_failure: 0,
    user_entered: 0,
    not_applicable: 0
  };
  for (const e of entries) counts[e.status] += 1;
  const sum = Object.values(counts).reduce((a, b) => a + b, 0);
  return sum === entries.length;
}
