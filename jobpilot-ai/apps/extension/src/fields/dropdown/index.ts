/**
 * The ONE dropdown fill path. Automatic autofill and the user's own choice in
 * the JobPilot widget both call `fillDropdown` — there is no separate simplified
 * widget path.
 *
 * Guarantees:
 *   • only one dropdown is open per frame at a time (global mutex/queue);
 *   • a click attempt is never success — every selection is verified by reading
 *     real DOM state, with one reopen + keyboard retry before failing;
 *   • a placeholder ("Select…") never counts as a value;
 *   • the first option is NEVER chosen as a fallback.
 */

import {
  aliasMatches,
  binaryAnswerMatches,
  companyCareersSourceMatches,
  dialCodeMatches,
  locationOptionMatches,
  normalizeForMatch,
  singletonPrivacyAcknowledgementMatches,
  singletonRequiredAffirmationMatches
} from "../aliases";
import type { DiscoveredField } from "../../types";
import { delay, focus, isElementVisible, key, TIMING } from "./dom";
import { focusTarget } from "./adapters/custom";
import { selectAdapter } from "./registry";
import type {
  AnswerSource,
  DropdownAdapter,
  DropdownEvent,
  DropdownFillResult,
  DropdownOption,
  DropdownReason
} from "./types";

export * from "./types";
export { selectAdapter, isDropdownField, DROPDOWN_ADAPTERS } from "./registry";
export { isBlankValue } from "./dom";

// --------------------------------------------------------------------------- //
// Per-frame mutex: never drive two dropdowns concurrently.
// --------------------------------------------------------------------------- //
let queue: Promise<unknown> = Promise.resolve();

export function withDropdownLock<T>(task: () => Promise<T>): Promise<T> {
  const run = queue.then(task, task);
  // Keep the chain alive even if a task rejects.
  queue = run.then(
    () => undefined,
    () => undefined
  );
  return run;
}

// --------------------------------------------------------------------------- //
// Diagnostics recorder (no answer values — labels/counts/codes only)
// --------------------------------------------------------------------------- //
/** Per-field event trail from the most recent attempt, for "Copy diagnostics".
 * Structural codes and counts ONLY — never the user's answer. */
const eventLog = new Map<string, DropdownEvent[]>();

export function dropdownEventLog(): Record<string, DropdownEvent[]> {
  return Object.fromEntries(eventLog);
}

export function clearDropdownEventLog(): void {
  eventLog.clear();
}

class Recorder {
  readonly events: DropdownEvent[] = [];
  constructor(private readonly uid: string) {}
  add(event: DropdownEvent["event"], detail?: DropdownEvent["detail"]): void {
    this.events.push({ event, uid: this.uid, detail });
  }
}

export interface DropdownFillOptions {
  /** Requested answer(s). Multi-select is an ARRAY, never comma-joined text. */
  values: string[];
  /** Optional query that reveals remote/search-only options. */
  searchValue?: string;
  /** Open the control purely to READ its options — an explicit user action in
   * the review widget. Never set during an autofill run: opening a menu we
   * cannot answer leaves the employer page visibly disturbed, which is exactly
   * how the live Airbnb page ended up with every dropdown open and blank. */
  allowProbe?: boolean;
  answerSource?: AnswerSource;
  /** Hook so the caller (widget) can hide an overlay covering the control. */
  beforeInteract?: () => void | Promise<void>;
  afterInteract?: () => void | Promise<void>;
}

/**
 * Drive one dropdown to a verified selection. Returns the option labels the
 * control actually offers even on failure, so the widget can present real
 * choices to the user.
 */
export async function fillDropdown(field: DiscoveredField, options: DropdownFillOptions): Promise<DropdownFillResult> {
  return withDropdownLock(() => fillDropdownUnlocked(field, options));
}

async function fillDropdownUnlocked(field: DiscoveredField, opts: DropdownFillOptions): Promise<DropdownFillResult> {
  const rec = new Recorder(field.uid);
  eventLog.set(field.uid, rec.events); // live reference: grows as the attempt runs
  rec.add("FIELD_DISCOVERED", { control: field.control, required: field.required });

  const adapter = selectAdapter(field);
  if (!adapter) {
    return fail(rec, "DROPDOWN_OPEN_FAILED", [], []);
  }
  rec.add("DROPDOWN_ADAPTER_SELECTED", { adapter: adapter.id });

  const wanted = opts.values.map((v) => v.trim()).filter(Boolean);
  if (wanted.length === 0) {
    // INVARIANT: never touch a control we have no answer for.
    //
    // This used to probe — open the menu to collect its real options for the
    // review widget. On the live Airbnb/Greenhouse page that left every
    // unanswered dropdown visibly opened and still reading "Select…", which is
    // precisely the reported symptom ("dropdowns open but no option is
    // selected"). Probing is now opt-in and belongs to an explicit user action,
    // never to an autofill run.
    if (!opts.allowProbe) {
      rec.add("SKIPPED_NO_TARGET");
      return {
        ok: false,
        reason: "SKIPPED_NO_TARGET",
        adapterId: adapter.id,
        // Read-only: readSelection never opens the control.
        selected: adapter.readSelection(field),
        options: [],
        events: rec.events
      };
    }
    const probe = await probeOptions(field, adapter, rec, opts);
    return {
      ok: false,
      reason: "ANSWER_MISSING",
      adapterId: adapter.id,
      selected: adapter.readSelection(field),
      options: probe,
      events: rec.events
    };
  }

  await opts.beforeInteract?.();
  try {
    const result = await attemptFill(field, adapter, wanted, rec, opts);
    return result;
  } finally {
    await adapter.close(field).catch(() => undefined);
    await opts.afterInteract?.();
  }
}

async function attemptFill(
  field: DiscoveredField,
  adapter: DropdownAdapter,
  wanted: string[],
  rec: Recorder,
  opts: DropdownFillOptions
): Promise<DropdownFillResult> {
  let availableLabels: string[] = [];

  // Two passes: the second reopens the control and uses the keyboard path.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const useKeyboard = attempt === 1;
    rec.add("DROPDOWN_OPEN_ATTEMPT", { attempt: attempt + 1, keyboard: useKeyboard });

    const opened = await adapter.open(field);
    if (!opened.ok) {
      if (attempt === 1) return fail(rec, opened.reason ?? "DROPDOWN_OPEN_FAILED", [], availableLabels, adapter.id);
      continue;
    }
    rec.add("DROPDOWN_OPENED", { adapter: adapter.id });

    let available = await adapter.getOptions(field, opened.listbox);
    // Location/autocomplete controls often open an EMPTY listbox and render
    // results only after a query. On the keyboard retry, type the caller's
    // precise search term before concluding that no options exist.
    if (available.length === 0 && useKeyboard) {
      const query = opts.searchValue ?? wanted[0];
      if (query) {
        typeSearch(field, adapter, query);
        available = await adapter.getOptions(field, opened.listbox);
      }
    }
    if (available.length === 0) {
      rec.add("OPTIONS_NOT_FOUND");
      if (attempt === 1) {
        return fail(rec, opened.listbox ? "OPTIONS_NOT_FOUND" : "LISTBOX_NOT_FOUND", [], availableLabels, adapter.id);
      }
      continue;
    }
    availableLabels = available.map((o) => o.label);
    rec.add("OPTIONS_DISCOVERED", { count: available.length });
    if (opts.answerSource) rec.add("ANSWER_SOURCE", { source: opts.answerSource });

    // ---- select every requested value (multi-select selects one at a time) --
    const verified: string[] = [];
    let lastFailure: DropdownReason | null = null;

    for (const value of wanted) {
      // Re-open + re-collect between selections: a multi-select menu often
      // closes after each pick, and options re-render.
      if (verified.length > 0) {
        const reopened = await adapter.open(field);
        if (!reopened.ok) {
          lastFailure = reopened.reason ?? "DROPDOWN_OPEN_FAILED";
          break;
        }
        available = await adapter.getOptions(field, reopened.listbox);
      }

      let option = matchOption(available, value);

      // Searchable control: type the exact label to reveal a filtered option.
      if (!option && useKeyboard) {
        typeSearch(field, adapter, opts.searchValue ?? value);
        available = await adapter.getOptions(field, null);
        option = matchOption(available, value);
      }
      if (!option) {
        lastFailure = "OPTION_NOT_AVAILABLE";
        continue;
      }
      rec.add("OPTION_MATCHED", { adapter: adapter.id });

      const selected = useKeyboard
        ? await selectViaKeyboard(field, adapter, option)
        : await adapter.select(field, option);
      if (!selected.ok) {
        lastFailure = selected.reason ?? "DROPDOWN_SELECTION_FAILED";
        continue;
      }
      rec.add("OPTION_CLICKED");

      // THE critical step: a click attempt is not success.
      if (await adapter.verify(field, option)) {
        rec.add("SELECTION_VERIFIED");
        verified.push(option.label);
      } else {
        lastFailure = "DROPDOWN_VERIFICATION_FAILED";
      }
    }

    if (verified.length === wanted.length) {
      return {
        ok: true,
        adapterId: adapter.id,
        answerSource: opts.answerSource,
        selected: adapter.readSelection(field),
        options: availableLabels,
        events: rec.events
      };
    }
    if (attempt === 1) {
      return fail(rec, lastFailure ?? "DROPDOWN_SELECTION_FAILED", adapter.readSelection(field), availableLabels, adapter.id);
    }
    // Fall through to the keyboard retry pass.
    await adapter.close(field).catch(() => undefined);
    await delay(80);
  }

  return fail(rec, "DROPDOWN_SELECTION_FAILED", adapter.readSelection(field), availableLabels, adapter.id);
}

/** Open just to read the real option labels (used when we have no answer). */
async function probeOptions(
  field: DiscoveredField,
  adapter: DropdownAdapter,
  rec: Recorder,
  opts: DropdownFillOptions
): Promise<string[]> {
  await opts.beforeInteract?.();
  try {
    const opened = await adapter.open(field);
    if (!opened.ok) {
      rec.add(opened.reason ?? "DROPDOWN_OPEN_FAILED");
      return field.options ?? [];
    }
    const available = await adapter.getOptions(field, opened.listbox);
    rec.add("OPTIONS_DISCOVERED", { count: available.length });
    return available.map((o) => o.label);
  } finally {
    await adapter.close(field).catch(() => undefined);
    await opts.afterInteract?.();
  }
}

/** Keyboard selection: move to the exact option and commit with Enter. Enter is
 * NEVER pressed unless the exact expected option is present. */
async function selectViaKeyboard(field: DiscoveredField, adapter: DropdownAdapter, option: DropdownOption) {
  const input = focusTarget(field);
  focus(input);
  // Walk the list to the target so the component's own highlight logic runs.
  for (let i = 0; i < 40; i += 1) {
    if (option.element.getAttribute("aria-selected") === "true" || isActiveDescendant(input, option.element)) break;
    key(input, "ArrowDown");
    await delay(TIMING.keyboardStepMs);
  }
  key(input, "Enter");
  // A searchable combobox's INPUT contains the query text before any option is
  // committed. `readSelection().length > 0` therefore mistakes "Tempe" for a
  // selected "Tempe, AZ". Verify the exact expected option instead.
  if (await adapter.verify(field, option)) return { ok: true } as const;
  // Fall back to a direct press on the option element.
  return adapter.select(field, option);
}

function isActiveDescendant(control: HTMLElement, option: HTMLElement): boolean {
  const id = control.getAttribute("aria-activedescendant");
  return Boolean(id && option.id && id === option.id);
}

function typeSearch(field: DiscoveredField, adapter: DropdownAdapter, text: string): void {
  const typed = (adapter as DropdownAdapter & { typeSearch?: (f: DiscoveredField, t: string) => void }).typeSearch;
  if (typed) typed(field, text);
}

/** Exact normalized match first, then the CONTROLLED alias table. Never a broad
 * fuzzy match, and never "just take the first option". */
function matchOption(options: DropdownOption[], value: string): DropdownOption | null {
  const usable = options.filter((o) => !o.disabled && isElementVisible(o.element));
  const substantive = usable.filter((o) => !/^(?:select|choose|please select)(?:\s+an?\s+option)?(?:\.{3}|…)?$/i.test(o.label.trim()));
  const target = normalizeForMatch(value);
  return (
    usable.find((o) => o.normalizedLabel === target) ??
    substantive.find((o) => companyCareersSourceMatches(o.label, value)) ??
    substantive.find((o) => singletonPrivacyAcknowledgementMatches(o.label, value, substantive.map((item) => item.label))) ??
    substantive.find((o) => singletonRequiredAffirmationMatches(o.label, value, substantive.map((item) => item.label))) ??
    usable.find((o) => aliasMatches(o.label, value)) ??
    // Explicit binary facts may be rendered as a verbose sentence by the ATS
    // (`authorized_us` -> "Yes, I am currently legally authorized…").
    usable.find((o) => binaryAnswerMatches(o.label, value)) ??
    usable.find((o) => locationOptionMatches(o.label, value)) ??
    // Dial codes only ("+1" -> "United States (+1)"); see dialCodeMatches.
    usable.find((o) => dialCodeMatches(o.label, value)) ??
    null
  );
}

function fail(
  rec: Recorder,
  reason: DropdownReason,
  selected: string[],
  options: string[],
  adapterId?: string
): DropdownFillResult {
  rec.add(reason);
  return { ok: false, reason, adapterId, selected, options, events: rec.events };
}
