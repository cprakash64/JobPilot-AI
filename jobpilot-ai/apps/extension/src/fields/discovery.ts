/**
 * Field discovery: scan a form for fillable fields and collect the accessible
 * metadata used for mapping. Prefers semantic attributes (label, aria, name)
 * over visual position. Skips hidden honeypots, disabled fields, and fields
 * outside the active application form.
 */

import type { DiscoveredField, FieldControl } from "../types";

const FIELD_SELECTOR = [
  "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image])",
  "textarea",
  "select"
].join(",");

let counter = 0;

export function discoverFields(root: ParentNode = document, step = 0): DiscoveredField[] {
  counter = 0;
  const elements = Array.from(root.querySelectorAll<HTMLElement>(FIELD_SELECTOR));
  const fields: DiscoveredField[] = [];
  const seenRadioGroups = new Set<string>();

  for (const el of elements) {
    if (!isFillable(el)) {
      continue;
    }
    const input = el as HTMLInputElement;
    if (input.type === "radio") {
      // Represent a radio group once, keyed by name.
      const groupKey = input.name || input.id;
      if (groupKey && seenRadioGroups.has(groupKey)) {
        continue;
      }
      if (groupKey) {
        seenRadioGroups.add(groupKey);
      }
    }
    fields.push(describe(el, step));
  }
  return fields;
}

export function isFillable(el: HTMLElement): boolean {
  const input = el as HTMLInputElement;
  if (input.disabled) {
    return false;
  }
  if (isHoneypot(el)) {
    return false;
  }
  if (!isVisible(el)) {
    return false;
  }
  return true;
}

function describe(el: HTMLElement, step: number): DiscoveredField {
  const input = el as HTMLInputElement & HTMLTextAreaElement & HTMLSelectElement;
  const control = controlOf(el);
  return {
    uid: `jp-${counter++}`,
    control,
    inputType: (input.type || el.tagName.toLowerCase()).toLowerCase(),
    name: input.name || "",
    id: el.id || "",
    autocomplete: (input.getAttribute("autocomplete") || "").toLowerCase(),
    placeholder: input.getAttribute("placeholder") || "",
    ariaLabel: input.getAttribute("aria-label") || ariaLabelledBy(el) || "",
    label: labelFor(el),
    nearbyText: nearbyText(el),
    sectionHeading: sectionHeading(el),
    required: input.required || input.getAttribute("aria-required") === "true",
    disabled: input.disabled,
    visible: true,
    existingValue: existingValue(el, control),
    options: optionsOf(el, control),
    validationMessage: (input as HTMLInputElement).validationMessage || "",
    step,
    element: el
  };
}

function controlOf(el: HTMLElement): FieldControl {
  const tag = el.tagName.toLowerCase();
  if (tag === "textarea") return "textarea";
  if (tag === "select") return "select";
  if (tag === "input") {
    const type = (el as HTMLInputElement).type;
    if (type === "radio") return "radio";
    if (type === "checkbox") return "checkbox";
    if (type === "file") return "file";
    return "text";
  }
  return "unknown";
}

function existingValue(el: HTMLElement, control: FieldControl): string {
  const input = el as HTMLInputElement;
  if (control === "checkbox" || control === "radio") {
    return input.checked ? "checked" : "";
  }
  if (control === "file") {
    return input.files && input.files.length ? input.files[0].name : "";
  }
  return (el as HTMLInputElement).value || "";
}

function optionsOf(el: HTMLElement, control: FieldControl): string[] {
  if (control === "select") {
    return Array.from((el as HTMLSelectElement).options).map((o) => o.textContent?.trim() || o.value);
  }
  if (control === "radio") {
    const name = (el as HTMLInputElement).name;
    if (!name) return [];
    const root = el.ownerDocument;
    return Array.from(root.querySelectorAll<HTMLInputElement>(`input[type=radio][name="${cssEscape(name)}"]`))
      .map((r) => labelFor(r) || r.value)
      .filter(Boolean);
  }
  return [];
}

function isHoneypot(el: HTMLElement): boolean {
  const style = (el.getAttribute("style") || "").toLowerCase();
  const name = ((el as HTMLInputElement).name || el.id || "").toLowerCase();
  if (/honeypot|leave.?blank|do.?not.?fill|bot.?field/.test(name)) return true;
  if (el.getAttribute("tabindex") === "-1" && /honeypot|bot|trap/.test(name)) return true;
  // Off-screen positioning is a classic honeypot hide.
  if (/(?:left|top)\s*:\s*-\d{3,}px/.test(style)) return true;
  return false;
}

function isVisible(el: HTMLElement): boolean {
  // We rely on explicit hiding signals (robust and layout-free) rather than
  // getClientRects/offsetParent, which are unavailable under jsdom and flaky.
  const style = (el.getAttribute("style") || "").toLowerCase();
  if (/display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?!\.)/.test(style)) return false;
  if (el.hidden) return false;
  if (el.getAttribute("aria-hidden") === "true") return false;
  if (el.getAttribute("type") === "hidden") return false;
  return true;
}

function labelFor(el: HTMLElement): string {
  const id = el.id;
  const doc = el.ownerDocument;
  if (id) {
    const label = doc.querySelector(`label[for="${cssEscape(id)}"]`);
    if (label?.textContent) return clean(label.textContent);
  }
  const wrapping = el.closest("label");
  if (wrapping?.textContent) return clean(wrapping.textContent);
  return "";
}

function ariaLabelledBy(el: HTMLElement): string {
  const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
  const doc = el.ownerDocument;
  const texts = ids.map((id) => doc.getElementById(id)?.textContent || "").filter(Boolean);
  return clean(texts.join(" "));
}

function nearbyText(el: HTMLElement): string {
  const container = el.closest("div,fieldset,section,li,p");
  // Only use a container that is specific to this field. A container holding
  // multiple controls (or the whole form) would leak other fields' labels —
  // e.g. a sensitive "Gender" label bleeding onto an unrelated text input.
  if (!container || container.querySelectorAll("input,textarea,select").length > 1) {
    return "";
  }
  const clone = container.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("input,textarea,select,option,script,style").forEach((n) => n.remove());
  return clean(clone.textContent || "").slice(0, 200);
}

function sectionHeading(el: HTMLElement): string {
  let node: HTMLElement | null = el;
  while (node) {
    const section = node.closest("section,fieldset") as HTMLElement | null;
    if (!section) break;
    const heading = section.querySelector("legend,h1,h2,h3,h4");
    if (heading?.textContent) return clean(heading.textContent);
    node = section.parentElement;
  }
  return "";
}

function clean(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}
