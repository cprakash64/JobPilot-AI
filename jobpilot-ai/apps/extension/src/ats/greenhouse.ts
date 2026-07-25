import type { ATSAdapter, DetectionResult, PageContext } from "../types";
import { baseDiscover, baseMap, findGenericSubmit, hostMatches } from "./base";

export const GreenhouseAdapter: ATSAdapter = {
  id: "greenhouse",
  displayName: "Greenhouse",
  detect(context: PageContext): DetectionResult {
    const { url, document: doc } = context;
    let confidence = 0;
    // Covers boards.greenhouse.io, job-boards.greenhouse.io (the current Affirm
    // host), embedded boards, and custom company Greenhouse domains.
    if (hostMatches(url, "greenhouse.io")) confidence += 0.6;
    // Legacy embed markup.
    if (doc.querySelector("#grnhse_app, #application_form, form#application-form")) confidence += 0.3;
    // Current React job-boards markup: field names are namespaced job_application[…]
    // and the greenhouse embed/app scripts/assets are present.
    if (doc.querySelector('input[name^="job_application"], [id^="job_application"]')) confidence += 0.3;
    if (doc.querySelector('script[src*="greenhouse.io"], link[href*="greenhouse.io"], [data-source="greenhouse"]')) {
      confidence += 0.2;
    }
    if (doc.querySelector('[aria-label*="application" i] , form[action*="greenhouse" i]')) confidence += 0.1;
    return { atsId: this.id, matched: confidence >= 0.5, confidence: Math.min(confidence, 1) };
  },
  discoverFields: (context) => baseDiscover(context),
  mapFields: (fields, session) => baseMap(fields, session),
  findSubmitControl: (context) =>
    context.document.querySelector<HTMLElement>("#submit_app, button#submit_application") ?? findGenericSubmit(context)
};
