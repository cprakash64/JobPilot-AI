/**
 * Form-filling engine. Deterministic DOM interaction with native setters so
 * React/Vue-controlled inputs register the change. Never overwrites a non-empty
 * value the user typed (unless forced), records what JobPilot filled so it can
 * be cleared, and applies subtle, fully-removable status highlighting.
 */

import type { DiscoveredField, FillOutcome } from "../types";

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
}

/** Fill one field with a value. Returns the outcome for the audit trail. */
export function fillField(field: DiscoveredField, value: string, options: FillOptions = {}): FillOutcome {
  const el = field.element;
  if (!el) {
    return { uid: field.uid, status: "error", reason: "no element" };
  }
  try {
    switch (field.control) {
      case "text":
      case "textarea":
        return fillTextLike(field, el as HTMLInputElement, value, options);
      case "select":
        return fillSelect(field, el as HTMLSelectElement, value, options);
      case "radio":
        return fillRadio(field, el as HTMLInputElement, value, options);
      case "checkbox":
        return fillCheckbox(field, el as HTMLInputElement, value, options);
      default:
        return { uid: field.uid, status: "skipped", reason: "unsupported control" };
    }
  } catch (err) {
    return { uid: field.uid, status: "error", reason: err instanceof Error ? err.message : "fill failed" };
  }
}

function fillTextLike(field: DiscoveredField, el: HTMLInputElement, value: string, options: FillOptions): FillOutcome {
  if (el.value && el.value.trim() && !options.force && !isJobPilotFilled(el)) {
    return { uid: field.uid, status: "skipped", reason: "user value present" };
  }
  captureOriginal(el);
  setNativeValue(el, value);
  dispatch(el, ["input", "change", "blur"]);
  mark(el, options.status ?? "verified");
  if (el.validationMessage) {
    mark(el, "invalid");
    return { uid: field.uid, status: "review_required", reason: el.validationMessage };
  }
  return { uid: field.uid, status: "filled" };
}

function fillSelect(field: DiscoveredField, el: HTMLSelectElement, value: string, options: FillOptions): FillOutcome {
  const target = value.trim().toLowerCase();
  const match = Array.from(el.options).find(
    (o) => o.value.toLowerCase() === target || (o.textContent || "").trim().toLowerCase() === target
  );
  if (!match) {
    return { uid: field.uid, status: "review_required", reason: "no matching option" };
  }
  if (el.value && el.selectedIndex > 0 && !options.force && !isJobPilotFilled(el)) {
    return { uid: field.uid, status: "skipped", reason: "user value present" };
  }
  captureOriginal(el);
  el.value = match.value;
  dispatch(el, ["input", "change"]);
  mark(el, options.status ?? "verified");
  return { uid: field.uid, status: "filled" };
}

function fillRadio(field: DiscoveredField, el: HTMLInputElement, value: string, _options: FillOptions): FillOutcome {
  const doc = el.ownerDocument;
  const group = Array.from(
    doc.querySelectorAll<HTMLInputElement>(`input[type=radio][name="${escape(el.name)}"]`)
  );
  const target = value.trim().toLowerCase();
  const match = group.find((r) => {
    const label = labelText(r).toLowerCase();
    return r.value.toLowerCase() === target || label === target || label.includes(target);
  });
  if (!match) {
    return { uid: field.uid, status: "review_required", reason: "no matching option" };
  }
  captureOriginal(match);
  if (!match.checked) {
    match.click(); // native toggle fires the framework's change handlers
  }
  dispatch(match, ["input", "change"]);
  mark(match, "verified");
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

function labelText(el: HTMLElement): string {
  const id = el.id;
  const doc = el.ownerDocument;
  if (id) {
    const label = doc.querySelector(`label[for="${escape(id)}"]`);
    if (label?.textContent) return label.textContent.replace(/\s+/g, " ").trim();
  }
  const wrap = el.closest("label");
  return wrap?.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

function escape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}
