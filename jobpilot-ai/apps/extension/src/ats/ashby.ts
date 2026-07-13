import type { ATSAdapter, DetectionResult, PageContext } from "../types";
import { baseDiscover, baseMap, findGenericSubmit, hostMatches } from "./base";

export const AshbyAdapter: ATSAdapter = {
  id: "ashby",
  displayName: "Ashby",
  detect(context: PageContext): DetectionResult {
    const { url, document: doc } = context;
    let confidence = 0;
    if (hostMatches(url, "ashbyhq.com")) confidence += 0.6;
    if (doc.querySelector('[class*="ashby"], [id*="ashby"], [data-source="ashby"]')) confidence += 0.3;
    if (doc.querySelector('script[src*="ashbyhq.com"], form[class*="_form"]')) confidence += 0.1;
    return { atsId: this.id, matched: confidence >= 0.5, confidence: Math.min(confidence, 1) };
  },
  discoverFields: (context) => baseDiscover(context),
  mapFields: (fields, session) => baseMap(fields, session),
  findSubmitControl: (context) =>
    context.document.querySelector<HTMLElement>("button[type=submit], [class*='ashby'] button[type=submit]") ??
    findGenericSubmit(context)
};
