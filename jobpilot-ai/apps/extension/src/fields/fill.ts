/**
 * Form-filling engine. Deterministic DOM interaction with native setters so
 * React/Vue-controlled inputs register the change. Never overwrites a non-empty
 * value the user typed (unless forced), records what JobPilot filled so it can
 * be cleared, and applies subtle, fully-removable status highlighting.
 */

import type { DiscoveredField, FillOutcome } from "../types";
import { fillDropdown, selectAdapter } from "./dropdown";
import type { AnswerSource } from "./dropdown/types";

export type FillStatus = "verified" | "generated" | "review" | "invalid" | "neutral";

const FILLED_ATTR = "data-jobpilot-filled";
const ORIGINAL_ATTR = "data-jobpilot-original";
const STATUS_ATTR = "data-jobpilot-status";

const OUTLINE: Record<FillStatus, string> = {
  verified: "2px solid #2f8f5b", // green — verified user data
  generated: "2px solid #2f6f9f", // blue — generated/suggested
  review: "2px solid #e0a72f", // yellow — review required
  invalid: "2px solid #c85a3e", // red — missing/invalid
  neutral: ""
};

export interface FillOptions {
  force?: boolean; // overwrite an existing non-empty user value
  status?: FillStatus;
  /** Where the answer came from — recorded in dropdown diagnostics. */
  answerSource?: AnswerSource;
  /** Let the caller move the JobPilot widget out of the way of a dropdown
   * menu before interacting, and restore it afterwards (section L). */
  beforeInteract?: () => void | Promise<void>;
  afterInteract?: () => void | Promise<void>;
  /** Optional post-write check against the value the control actually holds
   * once the site's own formatting/validation has run. Used for inputs that
   * legitimately rewrite what was typed (phone masks), where exact string
   * equality would report a false failure — and where no check at all would
   * report a false success. Returning false yields `review_required`. */
  verify?: (finalValue: string) => boolean;
  /** Text used to reveal options in a search-only dropdown. It may differ from
   * the full value used for exact option matching (e.g. search "Tempe" while
   * matching "Tempe, Arizona, United States"). */
  dropdownSearchValue?: string;
}

/** A text input that is really a custom-combobox affordance. */
function isComboboxLike(el: HTMLElement): boolean {
  const role = (el.getAttribute("role") || "").toLowerCase();
  if (role === "combobox") return true;
  if ((el.getAttribute("aria-haspopup") || "").toLowerCase() === "listbox") return true;
  if (el.getAttribute("aria-autocomplete")) return true;
  if (el.getAttribute("aria-controls") && el.getAttribute("aria-expanded") != null) return true;
  if (el.closest('[class*="-control"], [class*="__control"]')) return true;
  return false;
}

/** Fill one field with a value. Returns the outcome for the audit trail.
 * Async: custom/JS-controlled dropdowns (comboboxes, React Select, etc.) may
 * render their options asynchronously, so filling one control involves a
 * short, bounded wait — never a synchronous guess. Callers process fields
 * ONE AT A TIME (await each fillField before starting the next) so options
 * from two different open dropdowns can never be confused with each other. */
export async function fillField(field: DiscoveredField, value: string | string[], options: FillOptions = {}): Promise<FillOutcome> {
  const el = field.element;
  if (!el) {
    return { uid: field.uid, status: "error", reason: "no element" };
  }
  const single = Array.isArray(value) ? value[0] ?? "" : value;
  try {
    switch (field.control) {
      case "text":
      case "textarea":
        return await fillTextLike(field, el as HTMLInputElement, single, options);
      case "contenteditable":
        return fillContentEditable(field, el, single, options);
      // Every dropdown-like control — native select, ARIA combobox, React
      // Select, Greenhouse custom, searchable, multi-select, radio-as-options —
      // goes through the ONE verified dropdown adapter path.
      case "select":
      case "combobox":
      case "listbox":
      case "radio":
        return await fillViaDropdown(field, toValues(value), options);
      case "checkbox":
        return fillCheckbox(field, el as HTMLInputElement, single, options);
      default:
        return { uid: field.uid, status: "skipped", reason: "unsupported control" };
    }
  } catch (err) {
    return { uid: field.uid, status: "error", reason: err instanceof Error ? err.message : "fill failed" };
  }
}

/** Multi-select answers are ARRAYS. A legacy pipe-joined string is split for
 * backward compatibility, but arrays are the stored representation. */
function toValues(value: string | string[]): string[] {
  if (Array.isArray(value)) return value.map((v) => v.trim()).filter(Boolean);
  return value.includes("|") ? value.split("|").map((v) => v.trim()).filter(Boolean) : [value];
}

/** Drive a dropdown through the shared adapter and translate the verified
 * result into a FillOutcome. Never reports filled without DOM verification. */
async function fillViaDropdown(field: DiscoveredField, values: string[], options: FillOptions): Promise<FillOutcome> {
  const el = field.element as HTMLElement;
  const adapter = selectAdapter(field);
  if (!adapter) return { uid: field.uid, status: "skipped", reason: "unsupported control" };

  // Never overwrite a selection the user made themselves.
  const existing = adapter.readSelection(field);
  if (existing.length > 0 && !options.force && !isJobPilotFilled(el)) {
    return { uid: field.uid, status: "skipped", reason: "user value present" };
  }

  const result = await fillDropdown(field, {
    values,
    searchValue: options.dropdownSearchValue,
    answerSource: options.answerSource,
    beforeInteract: options.beforeInteract,
    afterInteract: options.afterInteract
  });
  const detail = {
    adapterId: result.adapterId,
    reasonCode: result.reason,
    options: result.options,
    selected: result.selected
  };
  if (result.ok) {
    captureOriginalIfPossible(el);
    mark(el, options.status ?? "verified");
    return { uid: field.uid, status: "filled", dropdown: detail };
  }
  // A failed dropdown is ALWAYS surfaced — highlighted red when required.
  mark(el, field.required ? "invalid" : "review");
  return { uid: field.uid, status: "review_required", reason: result.reason, dropdown: detail };
}

function captureOriginalIfPossible(el: HTMLElement): void {
  const candidate = el as HTMLInputElement | HTMLSelectElement;
  if (typeof candidate.value === "string") captureOriginal(candidate);
}

function fillContentEditable(field: DiscoveredField, el: HTMLElement, value: string, options: FillOptions): FillOutcome {
  if ((el.textContent || "").trim() && !options.force && !isJobPilotFilled(el)) return { uid: field.uid, status: "skipped", reason: "user value present" };
  el.setAttribute(ORIGINAL_ATTR, el.textContent || "");
  el.textContent = value;
  dispatch(el, ["input", "change", "blur"]);
  mark(el, options.status ?? "verified");
  return (el.textContent || "").trim() === value.trim() ? { uid: field.uid, status: "filled" } : { uid: field.uid, status: "error", reason: "value did not stick" };
}

async function fillTextLike(field: DiscoveredField, el: HTMLInputElement, value: string, options: FillOptions): Promise<FillOutcome> {
  if (el.value && el.value.trim() && !options.force && !isJobPilotFilled(el)) {
    return { uid: field.uid, status: "skipped", reason: "user value present" };
  }
  // A text input that is really a combobox affordance (React Select's inner
  // input, an aria-autocomplete field): typing alone never commits a selection,
  // so drive it through the verified dropdown adapter instead.
  if (isComboboxLike(el)) {
    return fillViaDropdown({ ...field, control: "combobox" }, [value], options);
  }
  captureOriginal(el);
  setNativeValue(el, value);
  dispatch(el, ["input", "change", "blur"]);
  mark(el, options.status ?? "verified");
  if (el.validationMessage) {
    mark(el, "invalid");
    return { uid: field.uid, status: "review_required", reason: el.validationMessage };
  }
  // The site may have rewritten (or rejected) the value on blur.
  if (options.verify && !options.verify(el.value ?? "")) {
    mark(el, field.required ? "invalid" : "review");
    return { uid: field.uid, status: "review_required", reason: "value did not survive site formatting" };
  }
  if (el.getAttribute("aria-invalid") === "true") {
    mark(el, "invalid");
    return { uid: field.uid, status: "review_required", reason: "site marked the field invalid" };
  }
  return { uid: field.uid, status: "filled" };
}

function fillCheckbox(field: DiscoveredField, el: HTMLInputElement, value: string, _options: FillOptions): FillOutcome {
  const desired = /^(true|yes|1|on|checked)$/i.test(value.trim());
  captureOriginal(el);
  if (el.checked !== desired) {
    el.click(); // native toggle to the desired state (avoids double-toggling)
    dispatch(el, ["input", "change"]);
  }
  mark(el, "verified");
  return { uid: field.uid, status: "filled" };
}

// --------------------------------------------------------------------------- //
// Clearing + highlighting (fully reversible; never permanently alters the page)
// --------------------------------------------------------------------------- //
export function clearJobPilotFields(root: ParentNode = document): number {
  const filled = Array.from(root.querySelectorAll<HTMLElement>(`[${FILLED_ATTR}]`));
  for (const el of filled) {
    const original = el.getAttribute(ORIGINAL_ATTR) ?? "";
    const tag = el.tagName.toLowerCase();
    const input = el as HTMLInputElement;
    if (input.type === "checkbox" || input.type === "radio") {
      input.checked = original === "checked";
    } else if (el.getAttribute("contenteditable") === "true") {
      el.textContent = original;
    } else if (tag === "select") {
      (el as HTMLSelectElement).value = original;
    } else {
      setNativeValue(el as HTMLInputElement | HTMLTextAreaElement, original);
    }
    dispatch(el, ["input", "change"]);
    el.removeAttribute(FILLED_ATTR);
    el.removeAttribute(ORIGINAL_ATTR);
    removeHighlight(el);
  }
  return filled.length;
}

export function highlight(el: HTMLElement, status: FillStatus): void {
  mark(el, status);
}

export function removeHighlight(el: HTMLElement): void {
  el.style.outline = "";
  el.style.removeProperty("outline-offset");
  el.removeAttribute(STATUS_ATTR);
}

export function isJobPilotFilled(el: HTMLElement): boolean {
  return el.hasAttribute(FILLED_ATTR);
}

// --------------------------------------------------------------------------- //
// Low-level helpers
// --------------------------------------------------------------------------- //
function mark(el: HTMLElement, status: FillStatus): void {
  el.setAttribute(FILLED_ATTR, "1");
  el.setAttribute(STATUS_ATTR, status);
  if (OUTLINE[status]) {
    el.style.outline = OUTLINE[status];
    el.style.outlineOffset = "1px";
  }
}

function captureOriginal(el: HTMLInputElement | HTMLSelectElement): void {
  if (el.hasAttribute(ORIGINAL_ATTR)) {
    return;
  }
  const input = el as HTMLInputElement;
  const original = input.type === "checkbox" || input.type === "radio"
    ? (input.checked ? "checked" : "")
    : el.value;
  el.setAttribute(ORIGINAL_ATTR, original ?? "");
}

function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const isTextarea = el.tagName.toLowerCase() === "textarea";
  const proto = isTextarea ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const protoSetter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  const instanceSetter = Object.getOwnPropertyDescriptor(el, "value")?.set;
  // React installs its own instance-level setter; call the prototype setter so
  // the value change is visible to React's synthetic event system.
  try {
    if (protoSetter && protoSetter !== instanceSetter) {
      protoSetter.call(el, value);
      return;
    }
  } catch {
    /* fall through to the plain assignment */
  }
  el.value = value;
}

function dispatch(el: HTMLElement, events: string[]): void {
  for (const type of events) {
    const event = type === "click" ? new MouseEvent("click", { bubbles: true }) : new Event(type, { bubbles: true });
    el.dispatchEvent(event);
  }
}
