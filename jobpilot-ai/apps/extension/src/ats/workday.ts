import type { ATSAdapter, DetectionResult, PageContext } from "../types";
import { baseDiscover, baseMap, hostMatches } from "./base";

function textOf(element: Element): string {
  return (element.textContent || (element as HTMLInputElement).value || "").replace(/\s+/g, " ").trim();
}

function usable(element: HTMLElement): boolean {
  const disabled = element.matches(":disabled") || element.getAttribute("aria-disabled") === "true";
  const style = element.ownerDocument.defaultView?.getComputedStyle(element);
  return !disabled && style?.display !== "none" && style?.visibility !== "hidden";
}

export function isWorkdayReviewPage(context: PageContext): boolean {
  const doc = context.document;
  const currentStep = doc.querySelector('[aria-current="step"], [data-automation-id*="progressBarActive" i]');
  if (currentStep && /^(review|preview)$/i.test(textOf(currentStep))) return true;
  const headings = Array.from(doc.querySelectorAll("h1,h2,[role=heading]"));
  return headings.some((heading) => /^(review|review your application|application review|preview)$/i.test(textOf(heading)));
}

export function findWorkdayNextControl(context: PageContext): HTMLElement | null {
  if (isWorkdayReviewPage(context)) return null;
  const controls = Array.from(context.document.querySelectorAll<HTMLElement>("button,[role=button],input[type=submit]"));
  // Exact and ordered allow-list. Workday's initial Apply action and the
  // resume-import path are safe transitions; Submit/Finish are never included.
  // Prefer resume import over an Apply button that may remain behind its modal.
  const allowedLabels = [
    "autofill with resume",
    "create account",
    "save and continue",
    "next",
    "continue",
    "apply"
  ];
  for (const label of allowedLabels) {
    const match = controls.find((control) => usable(control) && textOf(control).toLowerCase() === label);
    if (match) return match;
  }
  return null;
}

function findFinalSubmit(context: PageContext): HTMLElement | null {
  if (!isWorkdayReviewPage(context)) return null;
  const controls = Array.from(context.document.querySelectorAll<HTMLElement>("button,[role=button],input[type=submit]"));
  return controls.find((control) => usable(control) && /^(submit|submit application|apply)$/i.test(textOf(control))) ?? null;
}

export const WorkdayAdapter: ATSAdapter = {
  id: "workday",
  displayName: "Workday",
  detect(context: PageContext): DetectionResult {
    let confidence = 0;
    if (hostMatches(context.url, "myworkdayjobs.com") || hostMatches(context.url, "workday.com")) confidence += 0.75;
    if (context.document.querySelector('[data-automation-id], script[src*="workday" i], link[href*="workday" i]')) confidence += 0.2;
    return { atsId: this.id, matched: confidence >= 0.7, confidence: Math.min(confidence, 1) };
  },
  discoverFields: (context) => baseDiscover(context),
  mapFields: (fields, session) => baseMap(fields, session),
  findSubmitControl: findFinalSubmit,
  findNextControl: findWorkdayNextControl,
  isReviewPage: isWorkdayReviewPage
};
