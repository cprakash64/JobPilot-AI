import type { ATSAdapter, DetectionResult, PageContext } from "../types";
import { baseDiscover, baseMap, findGenericSubmit, hostMatches } from "./base";

export const LeverAdapter: ATSAdapter = {
  id: "lever",
  displayName: "Lever",
  detect(context: PageContext): DetectionResult {
    const { url, document: doc } = context;
    let confidence = 0;
    if (hostMatches(url, "lever.co")) confidence += 0.6;
    if (doc.querySelector(".application-form, form.application-form, [data-qa='application-form']")) confidence += 0.3;
    if (doc.querySelector('[class*="lever"], [data-source="lever"], script[src*="lever.co"]')) confidence += 0.2;
    return { atsId: this.id, matched: confidence >= 0.5, confidence: Math.min(confidence, 1) };
  },
  discoverFields: (context) => baseDiscover(context),
  mapFields: (fields, session) => baseMap(fields, session),
  findSubmitControl: (context) =>
    context.document.querySelector<HTMLElement>("button.template-btn-submit, button[type=submit]") ??
    findGenericSubmit(context)
};
