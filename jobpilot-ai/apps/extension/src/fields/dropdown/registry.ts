/**
 * Adapter registry. ATS-specific adapters are tried BEFORE the generic ones, so
 * a Greenhouse control is driven by Greenhouse rules even though it would also
 * satisfy the generic ARIA test.
 */

import type { DiscoveredField } from "../../types";
import { createCustomAdapter } from "./adapters/custom";
import { nativeSelectAdapter, radioGroupAdapter } from "./adapters/native";
import type { DropdownAdapter } from "./types";

const isCustomControl = (field: DiscoveredField): boolean =>
  field.control === "combobox" || field.control === "listbox";

function hasClass(field: DiscoveredField, fragment: string): boolean {
  const el = field.element as HTMLElement | undefined;
  if (!el) return false;
  if ((el.className || "").includes(fragment)) return true;
  return Boolean(el.querySelector(`[class*="${fragment}"]`) || el.closest(`[class*="${fragment}"]`));
}

/** Greenhouse's own custom select (job-boards React app). */
export const greenhouseDropdownAdapter: DropdownAdapter = createCustomAdapter({
  id: "greenhouse-custom",
  canHandle: (field) => {
    if (!isCustomControl(field)) return false;
    const el = field.element as HTMLElement | undefined;
    if (!el) return false;
    const doc = el.ownerDocument;
    const isGreenhouse =
      /greenhouse/i.test(doc.location?.hostname ?? "") ||
      doc.querySelector('script[src*="greenhouse"], [id^="job_application"], input[name^="job_application"]') !== null;
    return isGreenhouse;
  }
});

/** React Select (any version) — identified by its `__control`/`__menu` classes. */
export const reactSelectAdapter: DropdownAdapter = createCustomAdapter({
  id: "react-select",
  canHandle: (field) => isCustomControl(field) && (hasClass(field, "__control") || hasClass(field, "-control")),
  searchable: () => true
});

/** A searchable combobox: an input that filters its own option list. */
export const searchableComboboxAdapter: DropdownAdapter = createCustomAdapter({
  id: "searchable-combobox",
  canHandle: (field) => {
    if (!isCustomControl(field)) return false;
    const el = field.element as HTMLElement | undefined;
    if (!el) return false;
    const input = el.tagName.toLowerCase() === "input" ? el : el.querySelector('input:not([type="hidden"])');
    return Boolean(input && (el.getAttribute("aria-autocomplete") || input.getAttribute("aria-autocomplete")));
  },
  searchable: () => true
});

/** Generic ARIA combobox/listbox — the last-resort custom adapter. */
export const ariaComboboxAdapter: DropdownAdapter = createCustomAdapter({
  id: "aria-combobox",
  canHandle: isCustomControl
});

/** Order matters: ATS-specific → library-specific → generic → native. */
export const DROPDOWN_ADAPTERS: DropdownAdapter[] = [
  greenhouseDropdownAdapter,
  reactSelectAdapter,
  searchableComboboxAdapter,
  ariaComboboxAdapter,
  nativeSelectAdapter,
  radioGroupAdapter
];

export function selectAdapter(field: DiscoveredField): DropdownAdapter | null {
  return DROPDOWN_ADAPTERS.find((a) => a.canHandle(field)) ?? null;
}

/** True when this field is a dropdown-like control at all. */
export function isDropdownField(field: DiscoveredField): boolean {
  return selectAdapter(field) !== null;
}
