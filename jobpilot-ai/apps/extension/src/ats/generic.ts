import type { ATSAdapter, DetectionResult, PageContext } from "../types";
import { baseDiscover, baseMap, findGenericSubmit } from "./base";

/** Fallback for any standard HTML form. Matches with low confidence only when a
 * form with real fields is present, so it never activates on empty pages. */
export const GenericFormAdapter: ATSAdapter = {
  id: "generic",
  displayName: "Generic form",
  detect(context: PageContext): DetectionResult {
    const forms = Array.from(context.document.querySelectorAll("form"));
    const hasFields = forms.some((f) => f.querySelectorAll("input,textarea,select").length >= 2);
    return { atsId: this.id, matched: hasFields, confidence: hasFields ? 0.3 : 0 };
  },
  discoverFields: (context) => baseDiscover(context),
  mapFields: (fields, session) => baseMap(fields, session),
  findSubmitControl: (context) => findGenericSubmit(context)
};
