/**
 * Development-only "Copy diagnostics": a sanitized snapshot of the field ledger
 * so an unsupported application page can be debugged WITHOUT screenshots and,
 * critically, without leaking any personal data.
 *
 * Included: page origin/path (no query/token), detected ATS, field labels,
 * control types, canonical keys, required flags, option labels, statuses,
 * reason codes, frame ids, and the derived counts.
 *
 * NEVER included: entered values, the user's name/email/phone, resume contents,
 * tokens, or any saved profile answer. `fillSource` is coarse (e.g. "profile"),
 * never a value.
 */

import { dropdownEventLog } from "../fields/dropdown";
import { RESOLVER_VERSION, type FormRootResult } from "../ats/formRoot";
import type { LedgerCounts, LedgerEntry } from "../fields/ledger";
import type { FrameProbe } from "../frames/probe";

export interface DiagnosticsInput {
  url: string;
  atsId: string | null;
  ledger: LedgerEntry[];
  counts: LedgerCounts | null;
  /** The scored application-root resolution for this page (section A/I). */
  formRoot?: FormRootResult | null;
  /** Per-field label provenance: uid -> which wrapper rule produced the label. */
  labelSources?: Record<string, string>;
  extensionVersion?: string;
  /** This frame's sanitized application census (counts/scores only). */
  frameProbe?: FrameProbe | null;
  /** Build identity, so a stale loaded extension is immediately obvious. */
  build?: { version: string; builtAt: string; buildId: string };
}

export function buildDiagnostics(input: DiagnosticsInput): string {
  const snapshot = {
    generatedAt: new Date().toISOString(),
    page: safeUrl(input.url),
    detectedAts: input.atsId,
    extensionVersion: input.extensionVersion ?? null,
    resolverVersion: input.formRoot?.resolverVersion ?? RESOLVER_VERSION,
    // Build identity — the first thing to check when the live page disagrees
    // with the test suite, because a stale unpacked extension explains it.
    build: input.build ?? null,
    // This frame's census: what controls exist, whether a root resolved, and
    // WHY. A bare "could not identify form" is never enough to act on.
    frame: input.frameProbe
      ? {
          isTopFrame: input.frameProbe.isTopFrame,
          url: input.frameProbe.sanitizedUrl,
          readyState: input.frameProbe.readyState,
          controls: {
            forms: input.frameProbe.formCount,
            inputs: input.frameProbe.visibleInputs,
            textareas: input.frameProbe.visibleTextareas,
            selects: input.frameProbe.nativeSelects,
            ariaHaspopupButtons: input.frameProbe.ariaHaspopupButtons,
            roleComboboxes: input.frameProbe.roleComboboxes,
            required: input.frameProbe.requiredControls,
            fileInputs: input.frameProbe.fileInputs
          },
          openShadowRoots: input.frameProbe.openShadowRoots,
          applicationLabelsFound: input.frameProbe.applicationLabelsFound,
          candidateCount: input.frameProbe.candidateCount,
          bestScore: input.frameProbe.bestScore
        }
      : null,
    // Which container was chosen as the application form, why, and what was
    // rejected — the fastest way to diagnose a mis-scoped page.
    applicationRoot: input.formRoot
      ? {
          selected: input.formRoot.candidates.find((c) => !c.excluded)?.fingerprint ?? null,
          confident: input.formRoot.confident,
          reason: input.formRoot.reason ?? null,
          rootKind: input.formRoot.rootKind ?? null,
          // Plain-language verdict: which candidate won or why none did.
          explanation: input.formRoot.explanation ?? null,
          candidates: input.formRoot.candidates.map((c) => ({
            fingerprint: c.fingerprint,
            score: c.score,
            signals: c.signals,
            excluded: c.excluded ?? null,
            fieldCount: c.fieldCount,
            requiredCount: c.requiredCount
          }))
        }
      : null,
    counts: input.counts,
    fields: input.ledger.map((e) => ({
      uid: e.uid,
      frameId: e.frameId,
      label: e.label, // a QUESTION label, never an answer
      normalizedLabel: e.normalizedLabel,
      // Which wrapper rule produced this question text (label_for, legend, …).
      labelSource: input.labelSources?.[e.uid] ?? null,
      controlType: e.controlType,
      canonicalKey: e.canonicalKey,
      required: e.required,
      sensitive: e.sensitive,
      multiple: e.multiple,
      optionLabels: e.options,
      status: e.status,
      reasonCode: e.reasonCode || null,
      fillSource: e.fillSource,
      verified: e.verified
    })),
    // Per-dropdown event trail: FIELD_DISCOVERED → DROPDOWN_ADAPTER_SELECTED →
    // DROPDOWN_OPEN_ATTEMPT → DROPDOWN_OPENED → OPTIONS_DISCOVERED (count only)
    // → ANSWER_SOURCE → OPTION_MATCHED → OPTION_CLICKED → SELECTION_VERIFIED, or
    // the exact failure code. Contains no answer values.
    dropdowns: dropdownEventLog()
  };
  return JSON.stringify(snapshot, null, 2);
}

/** Origin + pathname only — strips query string, fragment, and any token. */
function safeUrl(raw: string): string {
  try {
    const u = new URL(raw);
    return `${u.origin}${u.pathname}`;
  } catch {
    return "(unparseable url)";
  }
}
