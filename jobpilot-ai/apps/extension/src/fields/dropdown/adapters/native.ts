/**
 * Native <select> and radio-group adapters. A native select needs no menu to
 * open, but it goes through the SAME interface so callers never branch — and so
 * its selection is verified from real DOM state exactly like a custom control.
 */

import { aliasMatches, normalizeForMatch } from "../../aliases";
import type { DiscoveredField } from "../../../types";
import { clean, isBlankValue, isDisabled, isElementVisible, isPlaceholderLabel, scrollIntoView } from "../dom";
import type { DropdownAdapter, DropdownOption, OpenResult, SelectResult } from "../types";

function labelForRadio(el: HTMLElement): string {
  const id = el.id;
  const doc = el.ownerDocument;
  if (id) {
    const label = doc.querySelector(`label[for="${cssEscape(id)}"]`);
    if (label?.textContent) return clean(label.textContent);
  }
  return clean(el.closest("label")?.textContent) || clean((el as HTMLInputElement).value);
}

function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(value);
  return value.replace(/["\\]/g, "\\$&");
}

export const nativeSelectAdapter: DropdownAdapter = {
  id: "native-select",
  canHandle: (field) => field.control === "select",

  async open(field): Promise<OpenResult> {
    const el = field.element as HTMLSelectElement | undefined;
    if (!el) return { ok: false, reason: "DROPDOWN_NOT_VISIBLE" };
    scrollIntoView(el);
    if (!isElementVisible(el)) return { ok: false, reason: "DROPDOWN_NOT_VISIBLE" };
    if (isDisabled(el)) return { ok: false, reason: "DROPDOWN_DISABLED" };
    // A native select has no menu to open — its options are already in the DOM.
    return { ok: true, listbox: el };
  },

  async getOptions(field): Promise<DropdownOption[]> {
    const el = field.element as HTMLSelectElement | undefined;
    if (!el) return [];
    return Array.from(el.options)
      .map((option, index) => {
        const label = clean(option.textContent) || option.value;
        return {
          id: option.value || `opt-${index}`,
          label,
          normalizedLabel: normalizeForMatch(label),
          disabled: option.disabled,
          selected: option.selected,
          element: option as unknown as HTMLElement
        };
      })
      // A placeholder ("Select...") is never a real option.
      .filter((o) => o.label && !isPlaceholderLabel(o.label));
  },

  async select(field, option): Promise<SelectResult> {
    const el = field.element as HTMLSelectElement | undefined;
    if (!el) return { ok: false, reason: "DROPDOWN_SELECTION_FAILED" };
    const target = Array.from(el.options).find(
      (o) => o === (option.element as unknown as HTMLOptionElement) || normalizeForMatch(clean(o.textContent) || o.value) === option.normalizedLabel
    );
    if (!target) return { ok: false, reason: "OPTION_NOT_AVAILABLE" };
    if (target.disabled) return { ok: false, reason: "OPTION_NOT_AVAILABLE" };
    if (el.multiple) target.selected = true;
    else el.value = target.value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true };
  },

  async verify(field, expected): Promise<boolean> {
    const wanted = typeof expected === "string" ? expected : expected.label;
    return nativeSelectAdapter
      .readSelection(field)
      .some((s) => aliasMatches(s, wanted) || normalizeForMatch(s) === normalizeForMatch(wanted));
  },

  async close(): Promise<void> {
    /* nothing to close */
  },

  readSelection(field): string[] {
    const el = field.element as HTMLSelectElement | undefined;
    if (!el) return [];
    return Array.from(el.selectedOptions ?? [])
      // ATS forms commonly use a human-readable first option such as
      // "Decline to self-identify" with value="" as the placeholder.  The
      // empty DOM value is authoritative here: treating its label as an
      // existing answer prevents a consented EEO value from ever replacing
      // it.  A real decline option with a non-empty value is still preserved.
      .filter((o) => o.value.trim() !== "")
      .map((o) => clean(o.textContent) || o.value)
      .filter((label) => label && !isBlankValue(label));
  }
};

/** A radio group rendered as a set of choices behaves like a single-select. */
export const radioGroupAdapter: DropdownAdapter = {
  id: "radio-group",
  canHandle: (field) => field.control === "radio",

  async open(field): Promise<OpenResult> {
    const el = field.element as HTMLElement | undefined;
    if (!el) return { ok: false, reason: "DROPDOWN_NOT_VISIBLE" };
    scrollIntoView(el);
    if (isDisabled(el)) return { ok: false, reason: "DROPDOWN_DISABLED" };
    return { ok: true, listbox: el.parentElement };
  },

  async getOptions(field): Promise<DropdownOption[]> {
    return radioMembers(field).map((r, index) => {
      const label = labelForRadio(r);
      return {
        id: r.id || `radio-${index}`,
        label,
        normalizedLabel: normalizeForMatch(label),
        disabled: r.disabled,
        selected: r.checked,
        element: r
      };
    }).filter((o) => o.label);
  },

  async select(_field, option): Promise<SelectResult> {
    const radio = option.element as HTMLInputElement;
    if (radio.disabled) return { ok: false, reason: "OPTION_NOT_AVAILABLE" };
    scrollIntoView(radio);
    if (!radio.checked) radio.click(); // native activation fires framework handlers
    radio.dispatchEvent(new Event("input", { bubbles: true }));
    radio.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true };
  },

  async verify(field, expected): Promise<boolean> {
    const wanted = typeof expected === "string" ? expected : expected.label;
    return radioGroupAdapter
      .readSelection(field)
      .some((s) => aliasMatches(s, wanted) || normalizeForMatch(s) === normalizeForMatch(wanted));
  },

  async close(): Promise<void> {
    /* nothing to close */
  },

  readSelection(field): string[] {
    return radioMembers(field)
      .filter((r) => r.checked)
      .map((r) => labelForRadio(r))
      .filter((label) => label && !isBlankValue(label));
  }
};

function radioMembers(field: DiscoveredField): HTMLInputElement[] {
  const el = field.element as HTMLInputElement | undefined;
  if (!el) return [];
  const name = el.name;
  if (!name) return [el];
  return Array.from(el.ownerDocument.querySelectorAll<HTMLInputElement>(`input[type=radio][name="${cssEscape(name)}"]`));
}
