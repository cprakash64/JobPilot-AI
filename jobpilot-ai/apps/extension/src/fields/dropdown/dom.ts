/**
 * Shared DOM primitives for driving custom dropdowns like a real user.
 *
 * The two things that make custom dropdowns work (and that value assignment can
 * never achieve): a COMPLETE pointer event sequence in the order component
 * libraries listen for, and portal-aware menu resolution — React Select and
 * friends render the menu under document.body, nowhere near the control.
 */

import { normalizeForMatch } from "../aliases";
import type { DropdownOption } from "./types";

/** Values that are ALWAYS blank (section K). A placeholder is never a value. */
const PLACEHOLDER_RE =
  /^(select\b.*|choose\b.*|please select.*|pick one.*|--+|—+|\.\.\.|none selected|no selection)$/i;

export function isBlankValue(raw: string | null | undefined): boolean {
  if (raw == null) return true;
  const text = raw.replace(/\s+/g, " ").trim();
  if (!text) return true;
  return PLACEHOLDER_RE.test(text);
}

export function isPlaceholderLabel(text: string): boolean {
  return isBlankValue(text);
}

export function clean(text: string | null | undefined): string {
  return (text ?? "").replace(/\s+/g, " ").trim();
}

// --------------------------------------------------------------------------- //
// Visibility / geometry
// --------------------------------------------------------------------------- //
/** Layout-aware where available, attribute-based under jsdom (no layout). */
export function isElementVisible(el: HTMLElement): boolean {
  if (!el.isConnected) return false;
  const style = (el.getAttribute("style") || "").toLowerCase();
  if (/display\s*:\s*none|visibility\s*:\s*hidden/.test(style)) return false;
  if (el.hidden || el.getAttribute("aria-hidden") === "true") return false;

  const tag = el.tagName.toLowerCase();
  // An <option> has no independent layout box — inside a closed <select> its
  // rect is always 0×0, yet it is perfectly selectable. Its visibility is the
  // select's, so never apply a geometry test to it.
  if (tag === "option" || tag === "optgroup") return true;

  const view = el.ownerDocument.defaultView;
  if (view?.getComputedStyle) {
    const computed = view.getComputedStyle(el);
    if (computed.display === "none" || computed.visibility === "hidden") return false;
  }
  // Geometry is only meaningful where the document actually lays out (a real
  // browser). Under jsdom every rect is 0×0, which would hide everything.
  if (documentHasLayout(el.ownerDocument) && typeof el.getBoundingClientRect === "function") {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
  }
  return true;
}

function documentHasLayout(doc: Document): boolean {
  const rect = doc.body?.getBoundingClientRect?.();
  return Boolean(rect && (rect.width > 0 || rect.height > 0));
}

export function isDisabled(el: HTMLElement): boolean {
  const input = el as HTMLInputElement;
  if (input.disabled) return true;
  if (el.getAttribute("aria-disabled") === "true") return true;
  return false;
}

export function scrollIntoView(el: HTMLElement): void {
  try {
    el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" as ScrollBehavior });
  } catch {
    try {
      el.scrollIntoView();
    } catch {
      /* jsdom may not implement scrollIntoView at all */
    }
  }
}

// --------------------------------------------------------------------------- //
// Event sequences
// --------------------------------------------------------------------------- //
// NOTE: `view` is deliberately omitted from every event init. It is not needed
// for handler dispatch, and passing a cross-realm window makes the constructor
// throw ("member view is not of type Window").
function makePointerEvent(type: string, view: Window | null): Event {
  const init: PointerEventInit = { bubbles: true, cancelable: true, composed: true, pointerId: 1, isPrimary: true, button: 0 };
  const Ctor = (view as unknown as { PointerEvent?: typeof PointerEvent })?.PointerEvent;
  if (typeof Ctor === "function") return new Ctor(type, init);
  // jsdom (and old browsers) lack PointerEvent — MouseEvent is the closest thing
  // and still exercises the component's mouse handlers.
  return new MouseEvent(type.replace("pointer", "mouse"), { bubbles: true, cancelable: true, composed: true, button: 0 });
}

function makeMouseEvent(type: string, _view: Window | null): MouseEvent {
  return new MouseEvent(type, { bubbles: true, cancelable: true, composed: true, button: 0, detail: 1 });
}

/** The FULL sequence component libraries expect. React Select opens on
 * `mousedown`; Radix/Headless UI listen for `pointerdown`; plain ARIA widgets
 * use `click`. Dispatching all of them in real order satisfies every one. */
export function pressSequence(el: HTMLElement): void {
  const view = el.ownerDocument.defaultView;
  el.dispatchEvent(makePointerEvent("pointerdown", view));
  el.dispatchEvent(makeMouseEvent("mousedown", view));
  el.dispatchEvent(makePointerEvent("pointerup", view));
  el.dispatchEvent(makeMouseEvent("mouseup", view));
  el.dispatchEvent(makeMouseEvent("click", view));
  if (typeof (el as HTMLElement & { click?: () => void }).click === "function") {
    // Native activation for <option>/<button>-backed controls. Guarded so a
    // double-activation can't toggle a checkbox-style option twice.
    const tag = el.tagName.toLowerCase();
    if (tag === "button" || tag === "option") el.click();
  }
}

export function key(el: HTMLElement, keyName: string): void {
  const init: KeyboardEventInit = { key: keyName, code: keyName, bubbles: true, cancelable: true, composed: true };
  const view = el.ownerDocument.defaultView as (Window & { KeyboardEvent?: typeof KeyboardEvent }) | null;
  const Ctor = view?.KeyboardEvent ?? (typeof KeyboardEvent !== "undefined" ? KeyboardEvent : null);
  if (!Ctor) return; // no keyboard-event support in this environment
  el.dispatchEvent(new Ctor("keydown", init));
  el.dispatchEvent(new Ctor("keyup", init));
}

export function focus(el: HTMLElement): void {
  try {
    el.focus({ preventScroll: true });
  } catch {
    el.focus?.();
  }
}

/** Set a value through React's own setter so the framework sees the change. */
export function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const proto = el.tagName.toLowerCase() === "textarea" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const protoSetter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  const instanceSetter = Object.getOwnPropertyDescriptor(el, "value")?.set;
  try {
    if (protoSetter && protoSetter !== instanceSetter) {
      protoSetter.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
  } catch {
    /* fall through */
  }
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

/**
 * Wait budgets. Real components settle in tens of milliseconds; these are
 * upper bounds, and `waitFor` returns as soon as the condition holds, so a
 * healthy dropdown costs one poll. Overridable so tests run fast.
 */
export const TIMING = {
  openPointerMs: 600,
  openKeyboardMs: 400,
  openEnterMs: 250,
  listboxMs: 400,
  optionsMs: 600,
  verifyMs: 600,
  keyboardStepMs: 10,
  pollStepMs: 25,
  /** Hard ceiling for ONE dropdown across both attempts. */
  perFieldBudgetMs: 5000
};

export function configureDropdownTiming(overrides: Partial<typeof TIMING>): void {
  Object.assign(TIMING, overrides);
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Poll `check` on a bounded schedule until it returns truthy. */
export async function waitFor<T>(check: () => T | null | undefined | false, timeoutMs = 600, stepMs = TIMING.pollStepMs): Promise<T | null> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = check();
    if (value) return value as T;
    if (Date.now() >= deadline) return null;
    await delay(stepMs);
  }
}

// --------------------------------------------------------------------------- //
// Menu / option resolution (portal-aware)
// --------------------------------------------------------------------------- //
export const OPTION_SELECTOR = [
  '[role="option"]',
  '[class*="__option"]',
  '[class*="-option"]:not([class*="-option-list"]):not([class*="-options"])',
  '[id*="-option-"]'
].join(",");

const MENU_SELECTOR = [
  '[role="listbox"]',
  '[class*="__menu"]',
  '[class*="-menu"]:not([class*="-menu-list"])',
  '[data-radix-popper-content-wrapper]'
].join(",");

/** Resolve the menu for a control: aria-controls/aria-owns first, then a
 * descendant menu, then a VISIBLE menu anywhere in the document (the portal
 * case). Never returns a menu that belongs to a different open control. */
export function resolveListbox(control: HTMLElement, ownedIds: string[] = []): HTMLElement | null {
  const doc = control.ownerDocument;
  // The control IS the listbox (an always-open multi-select list).
  if ((control.getAttribute("role") || "").toLowerCase() === "listbox" && hasOptions(control)) {
    return control;
  }
  const ids = [
    ...ownedIds,
    ...(control.getAttribute("aria-controls") || "").split(/\s+/),
    ...(control.getAttribute("aria-owns") || "").split(/\s+/)
  ].filter(Boolean);
  for (const id of ids) {
    const el = doc.getElementById(id);
    if (el && isElementVisible(el) && hasOptions(el)) return el;
  }
  // aria-activedescendant points AT an option; its menu is that option's ancestor.
  const active = control.getAttribute("aria-activedescendant");
  if (active) {
    const option = doc.getElementById(active);
    const menu = option?.closest(MENU_SELECTOR) as HTMLElement | null;
    if (menu && isElementVisible(menu)) return menu;
  }
  // A menu rendered inside the control's own wrapper.
  const wrapper = control.closest('[class*="__control"], [class*="-control"], [class*="__container"], [class*="-container"], div');
  const nested = wrapper?.querySelector<HTMLElement>(MENU_SELECTOR);
  if (nested && isElementVisible(nested) && hasOptions(nested)) return nested;

  // Portal: the ONLY visible menu with options in the document. If several are
  // visible we cannot attribute one to this control, so we refuse to guess.
  const visible = Array.from(doc.querySelectorAll<HTMLElement>(MENU_SELECTOR)).filter(
    (m) => isElementVisible(m) && hasOptions(m)
  );
  if (visible.length === 1) return visible[0];
  return null;
}

function hasOptions(menu: HTMLElement): boolean {
  return menu.querySelector(OPTION_SELECTOR) !== null || menu.tagName.toLowerCase() === "select";
}

/** Collect visible, enabled, non-placeholder options from a resolved menu. */
export function collectOptions(menu: HTMLElement): DropdownOption[] {
  const nodes = Array.from(menu.querySelectorAll<HTMLElement>(OPTION_SELECTOR));
  const seen = new Set<string>();
  const options: DropdownOption[] = [];
  nodes.forEach((el, index) => {
    if (!isElementVisible(el)) return;
    const label = clean(el.textContent);
    if (!label || isPlaceholderLabel(label)) return;
    const normalizedLabel = normalizeForMatch(label);
    if (seen.has(normalizedLabel)) return; // duplicate wrappers around one option
    seen.add(normalizedLabel);
    options.push({
      id: el.id || `${normalizedLabel}#${index}`,
      label,
      normalizedLabel,
      disabled: isDisabled(el),
      selected: el.getAttribute("aria-selected") === "true" || /(?:^|\s)[\w-]*(?:is-selected|--is-selected|selected)(?:\s|$)/.test(el.className || ""),
      element: el
    });
  });
  return options;
}

/** Close whatever menu is open, so two controls are never open at once. */
export function closeAnyOpenMenu(doc: Document, except?: HTMLElement | null): void {
  for (const control of Array.from(doc.querySelectorAll<HTMLElement>('[aria-expanded="true"]'))) {
    if (except && (control === except || control.contains(except))) continue;
    key(control, "Escape");
    control.setAttribute("aria-expanded", "false");
  }
}
