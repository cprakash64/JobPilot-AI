/**
 * "Teach JobPilot" — observe the user completing an application by hand, and
 * offer to remember each answer.
 *
 * Design: rather than trying to interpret every framework's events, we take a
 * SNAPSHOT of every discovered field's committed value, then re-read all values
 * after any TRUSTED user interaction inside the verified application root. Any
 * field whose committed value changed is a learned answer. That works uniformly
 * for native inputs, native selects, React Select, ARIA comboboxes, multi-selects
 * (arrays), and conditional fields that appear mid-flow — because values are read
 * through the SAME dropdown adapter used for autofill.
 *
 * Nothing is ever persisted here. Each learned answer is handed to the widget,
 * which asks the user what scope (if any) to remember it at.
 */

import { COMPANY_SCOPED_FIELDS, type CanonicalField } from "../fields/taxonomy";
import { selectAdapter } from "../fields/dropdown";
import { isBlankValue } from "../fields/dropdown/dom";
import { valuePresent } from "../fields/ledger";
import type { DiscoveredField } from "../types";

/** Where a learned answer may be remembered. */
export type LearnScope = "application" | "global" | "company" | "sensitive" | "none";

export interface LearnedAnswer {
  uid: string;
  /** Stable identity for this question across applications at this ATS. */
  fingerprint: string;
  ats: string | null;
  employer: string | null;
  question: string;
  normalizedQuestion: string;
  helpText: string;
  controlType: string;
  /** The employer's real option labels (empty for free-text controls). */
  options: string[];
  /** ALWAYS an array, so multi-select is represented natively. */
  chosen: string[];
  required: boolean;
  canonicalKey: string | null;
  sensitive: boolean;
  /** JobPilot's suggestion; the user always makes the final choice. */
  proposedScope: LearnScope;
  /** False when the scope proposal is a guess — the widget must ask. */
  scopeConfident: boolean;
}

export interface TeachOptions {
  root: ParentNode;
  /** Live fields, keyed by uid — the ONLY controls we will ever observe. */
  fields: () => Map<string, DiscoveredField>;
  ats: string | null;
  employer: string | null;
  canonicalKeyFor: (uid: string) => string | null;
  sensitiveFor: (uid: string) => boolean;
  onLearned: (answer: LearnedAnswer) => void;
  /** Re-discover after the DOM changes (conditional fields appearing). */
  onRescanNeeded?: () => void;
  /** Require `event.isTrusted` (default TRUE — always true in production). Only
   * unit tests set this false, because jsdom marks `isTrusted` non-configurable
   * so a genuine user event cannot be simulated. The guard itself is covered by
   * a dedicated test that feeds it real (untrusted) autofill events. */
  requireTrusted?: boolean;
}

const SETTLE_MS = 250;

/** Read a field's committed value as an ARRAY (multi-select native). */
export function readCommitted(field: DiscoveredField): string[] {
  const adapter = selectAdapter(field);
  if (adapter) return adapter.readSelection(field).filter((v) => !isBlankValue(v));
  const el = field.element as HTMLInputElement | undefined;
  if (!el) return [];
  if (el.type === "checkbox") return el.checked ? ["checked"] : [];
  if (el.type === "file") return el.files && el.files.length ? [el.files[0].name] : [];
  const value = (el.value ?? el.textContent ?? "").trim();
  return value && !isBlankValue(value) ? [value] : [];
}

/**
 * Propose a persistence scope from the canonical mapping. When the mapping is
 * unknown we deliberately propose the SAFEST scope (this application only) and
 * mark it unconfident, so the widget asks rather than inferring from field text.
 */
export function proposeScope(canonicalKey: string | null, sensitive: boolean): { scope: LearnScope; confident: boolean } {
  if (sensitive) return { scope: "sensitive", confident: true };
  if (!canonicalKey || canonicalKey === "unknown") return { scope: "application", confident: false };
  if (COMPANY_SCOPED_FIELDS.has(canonicalKey as CanonicalField)) return { scope: "company", confident: true };
  if (GLOBAL_PROFILE_KEYS.has(canonicalKey)) return { scope: "global", confident: true };
  if (APPLICATION_ONLY_KEYS.has(canonicalKey)) return { scope: "application", confident: true };
  return { scope: "application", confident: false };
}

/** Reusable across every employer (stable facts about the candidate). */
const GLOBAL_PROFILE_KEYS: ReadonlySet<string> = new Set([
  "first_name", "last_name", "full_name", "preferred_name", "preferred_first_name",
  "preferred_last_name", "pronouns", "email", "phone", "address", "city", "state",
  "postal_code", "country", "linkedin_url", "github_url", "portfolio_url",
  "work_authorization_us", "sponsorship_required_now", "sponsorship_required_future",
  "current_company", "current_title", "years_of_experience", "willing_to_relocate",
  "preferred_workplace"
]);

/** Meaningful only for THIS application (consent, acknowledgements, essays). */
const APPLICATION_ONLY_KEYS: ReadonlySet<string> = new Set([
  "custom_motivation", "custom_experience", "salary_expectation", "available_start_date",
  "legal_attestation"
]);

/** Stable identity for a question so a learned answer can be found again. */
export function fieldFingerprint(field: DiscoveredField, ats: string | null): string {
  const key = field.normalizedLabel || field.name || field.id || field.uid;
  return `${ats ?? "generic"}::${field.control}::${key}`;
}

/**
 * Start observing. Returns a stop() function. Only TRUSTED interactions inside
 * `root` are considered; synthetic events (including JobPilot's own autofill)
 * are ignored, so autofill can never be mistaken for the user teaching.
 */
export function startTeachMode(options: TeachOptions): () => void {
  const target = options.root as unknown as EventTarget & ParentNode;
  const snapshot = new Map<string, string>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const key = (values: string[]): string => values.join("");

  // Baseline: whatever is already committed is NOT something the user just taught.
  for (const [uid, field] of options.fields()) snapshot.set(uid, key(readCommitted(field)));

  const reconcile = (): void => {
    if (stopped) return;
    const fields = options.fields();
    for (const [uid, field] of fields) {
      if (!field.element?.isConnected) continue;
      const values = readCommitted(field);
      const now = key(values);
      const before = snapshot.get(uid);
      if (before === undefined) {
        // A conditional field that appeared mid-flow: baseline it silently.
        snapshot.set(uid, now);
        continue;
      }
      if (now === before) continue;
      snapshot.set(uid, now);
      if (values.length === 0) continue; // cleared — nothing to remember

      const canonicalKey = options.canonicalKeyFor(uid);
      const sensitive = options.sensitiveFor(uid);
      const { scope, confident } = proposeScope(canonicalKey, sensitive);
      options.onLearned({
        uid,
        fingerprint: fieldFingerprint(field, options.ats),
        ats: options.ats,
        employer: options.employer,
        question: field.label || field.ariaLabel || field.placeholder || field.name,
        normalizedQuestion: field.normalizedLabel,
        helpText: field.nearbyText.slice(0, 200),
        controlType: field.control,
        options: field.options,
        chosen: values,
        required: field.required,
        canonicalKey,
        sensitive,
        proposedScope: scope,
        scopeConfident: confident
      });
    }
  };

  const schedule = (event: Event): void => {
    // Only real user input teaches JobPilot. `isTrusted` is false for every
    // event JobPilot itself dispatches during autofill.
    if (options.requireTrusted !== false && !event.isTrusted) return;
    const node = event.target as Node | null;
    if (!node || !(options.root as Element).contains?.(node)) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      options.onRescanNeeded?.();
      reconcile();
    }, SETTLE_MS);
  };

  const events = ["change", "input", "focusout", "click", "keyup"];
  for (const type of events) target.addEventListener(type, schedule, true);

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    for (const type of events) target.removeEventListener(type, schedule, true);
  };
}

/** True when a field currently holds a real (non-placeholder) value. */
export function fieldAnswered(field: DiscoveredField): boolean {
  return valuePresent(field);
}
