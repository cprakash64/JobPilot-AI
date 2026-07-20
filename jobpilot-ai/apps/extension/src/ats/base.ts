/**
 * Shared adapter helpers. All ATS adapters reuse the generic discovery, mapping,
 * and submit-locating logic; each only customizes detection (and, later, upload
 * quirks). This keeps ATS-specific code small and avoids one monolithic file.
 */

import { discoverFields } from "../fields/discovery";
import { buildMappings } from "../fields/mapping";
import type { ApplicationSessionData, DiscoveredField, FieldMappingResult, PageContext } from "../types";
import { resolveApplicationRoot, type FormRootResult } from "./formRoot";

/**
 * Resolve the application form root by weighted evidence (see formRoot.ts).
 *
 * Previously this picked the `<form>` with the most inputs and fell back to the
 * whole document — which selected the site's SEARCH form on a React application
 * that renders no `<form>` at all. Discovery is now scoped to a scored,
 * confidently-identified application root, or to nothing at all.
 *
 * Returns `null` when no candidate is confidently the application
 * (APPLICATION_FORM_AMBIGUOUS / NO_APPLICATION_FORM) — callers must surface that
 * rather than scanning the page globally.
 */
export function resolveApplicationForm(doc: Document): FormRootResult {
  return resolveApplicationRoot(doc);
}

/** Back-compatible accessor: the resolved root, or `document` only when the
 * caller has no way to report ambiguity. Prefer `resolveApplicationForm`. */
export function pickApplicationForm(doc: Document): ParentNode {
  return resolveApplicationRoot(doc).root ?? doc;
}

export function baseDiscover(context: PageContext, step = 0): DiscoveredField[] {
  return discoverFields(pickApplicationForm(context.document), step);
}

export function baseMap(fields: DiscoveredField[], session: ApplicationSessionData): FieldMappingResult {
  return buildMappings(fields, session);
}

/** Locate the final submit control — for WARNING only. JobPilot never clicks it. */
export function findGenericSubmit(context: PageContext): HTMLElement | null {
  const doc = context.document;
  const explicit = doc.querySelector<HTMLElement>("button[type=submit], input[type=submit]");
  if (explicit) {
    return explicit;
  }
  const buttons = Array.from(doc.querySelectorAll<HTMLElement>("button, [role=button]"));
  return buttons.find((b) => /\b(submit|apply|send application)\b/i.test(b.textContent || "")) ?? null;
}

export function hostMatches(url: string, fragment: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host === fragment || host.endsWith("." + fragment) || host.includes(fragment);
  } catch {
    return false;
  }
}

/**
 * Discover real file inputs for document upload, INCLUDING inputs that ordinary
 * field discovery skips because they are visually hidden behind an "Attach"
 * button (common on modern Greenhouse/Ashby). Each is classified as resume or
 * cover-letter from its associated label, section text, accepted MIME types, or
 * the nearby "Attach"/"Upload" button text — so the cover letter is never set
 * into the resume field.
 */
export function discoverUploadInputs(root: ParentNode): { input: HTMLInputElement; kind: "resume" | "cover-letter" }[] {
  const inputs = Array.from(root.querySelectorAll<HTMLInputElement>('input[type="file"]'));
  const out: { input: HTMLInputElement; kind: "resume" | "cover-letter" }[] = [];
  for (const input of inputs) {
    if (input.disabled) continue;
    const kind = classifyUpload(input);
    if (kind) out.push({ input, kind });
  }
  return out;
}

function classifyUpload(input: HTMLInputElement): "resume" | "cover-letter" | null {
  const context = uploadContextText(input);
  if (/cover\s*letter|coverletter|motivation letter/.test(context)) return "cover-letter";
  if (/resume|résumé|\bcv\b|curriculum vitae/.test(context)) return "resume";
  // A file input near an "attach/upload" control with no clear label is treated
  // as a resume only when it is the sole file input; otherwise left for review.
  return null;
}

function uploadContextText(input: HTMLInputElement): string {
  const parts: string[] = [];
  const id = input.id;
  const doc = input.ownerDocument;
  if (id) {
    const label = doc.querySelector(`label[for="${cssEscape(id)}"]`);
    if (label?.textContent) parts.push(label.textContent);
  }
  parts.push(input.getAttribute("name") || "", input.id || "", input.getAttribute("aria-label") || "");
  const labelledby = (input.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
  for (const lid of labelledby) parts.push(doc.getElementById(lid)?.textContent || "");
  const container = input.closest("div,fieldset,section,li");
  if (container) {
    // Include button/label text within the same upload block ("Attach", etc.).
    parts.push(container.querySelector("label,legend,h1,h2,h3,h4,button,[role=button]")?.textContent || "");
    parts.push((container as HTMLElement).getAttribute?.("data-field") || "");
  }
  return parts.join(" ").toLowerCase().replace(/\s+/g, " ");
}

function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(value);
  return value.replace(/["\\]/g, "\\$&");
}
