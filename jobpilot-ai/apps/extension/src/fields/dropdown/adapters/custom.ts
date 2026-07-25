/**
 * Custom (non-native) dropdown adapter: ARIA combobox, React Select, the
 * Greenhouse custom control, and searchable comboboxes. They differ only in
 * which element is interactive and how their menu is rendered, so one
 * configurable implementation covers all of them — and each is still exposed as
 * its own adapter so ATS-specific behaviour can diverge later.
 */

import { aliasMatches, normalizeForMatch } from "../../aliases";
import type { DiscoveredField } from "../../../types";
import {
  clean,
  closeAnyOpenMenu,
  collectOptions,
  focus,
  isBlankValue,
  isDisabled,
  isElementVisible,
  key,
  pressSequence,
  resolveListbox,
  scrollIntoView,
  setNativeValue,
  TIMING,
  waitFor
} from "../dom";
import type { DropdownAdapter, DropdownOption, OpenResult, SelectResult } from "../types";

export interface CustomAdapterConfig {
  id: string;
  /** Decide whether this adapter owns the field. Checked in registry order. */
  canHandle(field: DiscoveredField): boolean;
  /** True when the control filters options from typed text. */
  searchable?(field: DiscoveredField): boolean;
}

/** The element that must receive the pointer sequence to open the menu.
 * React Select listens on its `__control` wrapper, not the inner input. */
export function pressTarget(field: DiscoveredField): HTMLElement {
  const el = field.element as HTMLElement;
  const control = el.querySelector<HTMLElement>('[class*="__control"], [class*="-control"]');
  if (control) return control;
  const closestControl = el.closest<HTMLElement>('[class*="__control"], [class*="-control"]');
  if (closestControl && closestControl !== el) return closestControl;
  const role = (el.getAttribute("role") || "").toLowerCase();
  if (role === "combobox" || role === "listbox" || el.getAttribute("aria-haspopup") === "listbox") return el;
  const inner = el.querySelector<HTMLElement>('[role="combobox"], [aria-haspopup="listbox"], button');
  return inner ?? el;
}

/** The element that should hold focus / receive typed search text. */
export function focusTarget(field: DiscoveredField): HTMLElement {
  const el = field.element as HTMLElement;
  if (el.tagName.toLowerCase() === "input") return el;
  const input = el.querySelector<HTMLElement>('input:not([type="hidden"])');
  return input ?? pressTarget(field);
}

/** True once the control reports open, or a menu with options is resolvable. */
function openIndicator(field: DiscoveredField): HTMLElement | null {
  const el = field.element as HTMLElement;
  const target = pressTarget(field);
  const expanded = el.getAttribute("aria-expanded") === "true" || target.getAttribute("aria-expanded") === "true";
  const listbox = resolveListbox(el) ?? resolveListbox(target);
  if (listbox) return listbox;
  // A remote autocomplete can be genuinely open while its owned listbox is
  // still empty; options appear only after typing. Resolve that specific owned
  // menu without requiring an option, but only after aria-expanded confirms the
  // control is open. Portal-wide empty-menu guessing remains forbidden.
  if (expanded) {
    for (const owner of [el, target]) {
      for (const attr of ["aria-controls", "aria-owns"]) {
        for (const id of (owner.getAttribute(attr) || "").split(/\s+/).filter(Boolean)) {
          const owned = owner.ownerDocument.getElementById(id);
          if (owned && isElementVisible(owned)) return owned;
        }
      }
    }
  }
  return null;
}

export function createCustomAdapter(config: CustomAdapterConfig): DropdownAdapter {
  const adapter: DropdownAdapter = {
    id: config.id,
    canHandle: config.canHandle,

    async open(field): Promise<OpenResult> {
      const el = field.element as HTMLElement | undefined;
      if (!el) return { ok: false, reason: "DROPDOWN_NOT_VISIBLE" };
      scrollIntoView(el);
      if (!isElementVisible(el)) return { ok: false, reason: "DROPDOWN_NOT_VISIBLE" };
      if (isDisabled(el)) return { ok: false, reason: "DROPDOWN_DISABLED" };

      // Only one dropdown may be open per frame — close anything else first so
      // options from another control can never be mistaken for these.
      closeAnyOpenMenu(el.ownerDocument, el);

      const press = pressTarget(field);
      const focusEl = focusTarget(field);

      // 1. Pointer path: the full pointerdown→mousedown→pointerup→mouseup→click
      //    sequence, which is what React Select / Radix / ARIA widgets listen for.
      focus(focusEl);
      pressSequence(press);
      let listbox = await waitFor(() => openIndicator(field), TIMING.openPointerMs);

      // 2. Keyboard fallback: focus + ArrowDown is the standard ARIA open.
      if (!listbox) {
        focus(focusEl);
        key(focusEl, "ArrowDown");
        listbox = await waitFor(() => openIndicator(field), TIMING.openKeyboardMs);
      }
      if (!listbox) {
        key(focusEl, "Enter");
        listbox = await waitFor(() => openIndicator(field), TIMING.openEnterMs);
      }
      if (!listbox) return { ok: false, reason: "DROPDOWN_OPEN_FAILED" };
      return { ok: true, listbox };
    },

    async getOptions(field, listbox): Promise<DropdownOption[]> {
      const el = field.element as HTMLElement;
      const menu = listbox ?? (await waitFor(() => resolveListbox(el), TIMING.listboxMs));
      if (!menu) return [];
      // Options may stream in asynchronously (remote-loaded country lists).
      const options = await waitFor(() => {
        const found = collectOptions(menu);
        return found.length > 0 ? found : null;
      }, TIMING.optionsMs);
      return options ?? [];
    },

    async select(_field, option): Promise<SelectResult> {
      if (option.disabled) return { ok: false, reason: "OPTION_NOT_AVAILABLE" };
      scrollIntoView(option.element);
      if (!isElementVisible(option.element)) return { ok: false, reason: "OPTION_NOT_AVAILABLE" };
      pressSequence(option.element);
      return { ok: true };
    },

    async verify(field, expected): Promise<boolean> {
      const wanted = typeof expected === "string" ? expected : expected.label;
      // React state settles asynchronously; poll rather than reading once.
      const ok = await waitFor(() => {
        const selected = adapter.readSelection(field);
        return selected.some((s) => aliasMatches(s, wanted) || normalizeForMatch(s) === normalizeForMatch(wanted)) ? true : null;
      }, TIMING.verifyMs);
      return Boolean(ok);
    },

    async close(field): Promise<void> {
      const el = field.element as HTMLElement | undefined;
      if (!el) return;
      key(focusTarget(field), "Escape");
      (focusTarget(field) as HTMLElement & { blur?: () => void }).blur?.();
    },

    readSelection(field): string[] {
      return readCustomSelection(field);
    }
  };

  if (config.searchable) {
    /** Searchable controls: type the exact label to filter, then pick the exact
     * visible match. Never press Enter when the exact option is absent. */
    (adapter as DropdownAdapter & { typeSearch?: (f: DiscoveredField, t: string) => void }).typeSearch = (f, text) => {
      const input = focusTarget(f);
      if (input.tagName.toLowerCase() !== "input") return;
      focus(input);
      setNativeValue(input as HTMLInputElement, text);
    };
  }
  return adapter;
}

/**
 * Read the control's REAL current selection. Covers every shape a custom
 * dropdown stores its value in; placeholders are filtered out so "Select..."
 * can never be mistaken for an answer.
 */
export function readCustomSelection(field: DiscoveredField): string[] {
  const el = field.element as HTMLElement | undefined;
  if (!el) return [];
  const scope = (el.closest<HTMLElement>('[class*="__container"], [class*="-container"], .field, div') ?? el) as HTMLElement;
  const labels: string[] = [];

  // 1. React Select multi-value chips (and any multi-select chip UI).
  for (const chip of Array.from(el.querySelectorAll<HTMLElement>('[class*="multiValue"] , [class*="multi-value"]'))) {
    const text = clean(chip.textContent).replace(/[×✕✖]\s*$/, "").trim();
    if (text && !isBlankValue(text)) labels.push(text);
  }
  if (labels.length > 0) return dedupe(labels);

  // 2. React Select single value / generic display element.
  const single = el.querySelector<HTMLElement>('[class*="singleValue"], [class*="single-value"]');
  if (single && !isBlankValue(clean(single.textContent))) return [clean(single.textContent)];

  // 3. aria-selected options still present in the DOM (ARIA listboxes).
  const selected = Array.from(el.querySelectorAll<HTMLElement>('[role="option"][aria-selected="true"]'))
    .map((o) => clean(o.textContent))
    .filter((t) => t && !isBlankValue(t));
  if (selected.length > 0) return dedupe(selected);

  // 4. An owned listbox elsewhere (portal) whose options are marked selected.
  const owned = resolveListbox(el);
  if (owned) {
    const ownedSelected = Array.from(owned.querySelectorAll<HTMLElement>('[role="option"][aria-selected="true"]'))
      .map((o) => clean(o.textContent))
      .filter((t) => t && !isBlankValue(t));
    if (ownedSelected.length > 0) return dedupe(ownedSelected);
  }

  // 5. The control's own input value (searchable combobox with a committed value).
  const input = el.tagName.toLowerCase() === "input" ? (el as HTMLInputElement) : el.querySelector<HTMLInputElement>('input:not([type="hidden"])');
  if (input && !isBlankValue(input.value)) return [clean(input.value)];

  // 6. The hidden input a custom control posts (React Select's hidden field).
  const hidden = scope.querySelector<HTMLInputElement>('input[type="hidden"]');
  if (hidden && !isBlankValue(hidden.value)) return [clean(hidden.value)];

  // 7. Last resort: the control's rendered text, minus any placeholder node.
  const clone = el.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('[class*="placeholder"], [role="listbox"], [role="option"], input, svg, button').forEach((n) => n.remove());
  const text = clean(clone.textContent);
  return text && !isBlankValue(text) ? [text] : [];
}

function dedupe(values: string[]): string[] {
  const seen = new Set<string>();
  return values.filter((v) => {
    const k = normalizeForMatch(v);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
