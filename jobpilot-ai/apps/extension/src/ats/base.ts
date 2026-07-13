/**
 * Shared adapter helpers. All ATS adapters reuse the generic discovery, mapping,
 * and submit-locating logic; each only customizes detection (and, later, upload
 * quirks). This keeps ATS-specific code small and avoids one monolithic file.
 */

import { discoverFields } from "../fields/discovery";
import { buildMappings } from "../fields/mapping";
import type { ApplicationSessionData, DiscoveredField, FieldMappingResult, PageContext } from "../types";

/** Choose the form most likely to be the application (the one with most fields)
 * so we never fill unrelated search/newsletter inputs elsewhere on the page. */
export function pickApplicationForm(doc: Document): ParentNode {
  const forms = Array.from(doc.querySelectorAll("form"));
  if (forms.length === 0) {
    return doc;
  }
  let best: Element = forms[0];
  let bestCount = -1;
  for (const form of forms) {
    const count = form.querySelectorAll("input,textarea,select").length;
    if (count > bestCount) {
      best = form;
      bestCount = count;
    }
  }
  return best;
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
