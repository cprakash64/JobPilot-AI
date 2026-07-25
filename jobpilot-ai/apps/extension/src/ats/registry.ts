/**
 * ATS adapter registry + deterministic detection. Specific adapters are tried
 * first; the generic HTML-form adapter is the fallback.
 */

import type { ATSAdapter, DetectionResult, PageContext } from "../types";
import { AshbyAdapter } from "./ashby";
import { hostMatches } from "./base";
import { GenericFormAdapter } from "./generic";
import { GreenhouseAdapter } from "./greenhouse";
import { LeverAdapter } from "./lever";
import { WorkdayAdapter } from "./workday";

export const SPECIFIC_ADAPTERS: ATSAdapter[] = [GreenhouseAdapter, LeverAdapter, AshbyAdapter, WorkdayAdapter];
export const ADAPTERS: ATSAdapter[] = [...SPECIFIC_ADAPTERS, GenericFormAdapter];

export interface DetectionOutcome {
  adapter: ATSAdapter;
  result: DetectionResult;
  limited: boolean; // e.g. Workday — detected, standard fields only
}

export function detectAdapter(context: PageContext): DetectionOutcome | null {
  let best: { adapter: ATSAdapter; result: DetectionResult } | null = null;
  for (const adapter of SPECIFIC_ADAPTERS) {
    const result = adapter.detect(context);
    if (result.matched && (!best || result.confidence > best.result.confidence)) {
      best = { adapter, result };
    }
  }
  if (best) {
    return { ...best, limited: false };
  }

  const genericHosts: Array<[string, string]> = [
    ["smartrecruiters.com", "smartrecruiters"], ["rippling.com", "rippling"],
    ["bamboohr.com", "bamboohr"], ["applytojob.com", "jazzhr"],
    ["jazz.co", "jazzhr"], ["teamtailor.com", "teamtailor"]
  ];
  const matchedHost = genericHosts.find(([host]) => hostMatches(context.url, host));
  if (matchedHost) {
    return { adapter: GenericFormAdapter, result: { atsId: matchedHost[1], matched: true, confidence: 0.7 }, limited: true };
  }

  const generic = GenericFormAdapter.detect(context);
  if (generic.matched) {
    return { adapter: GenericFormAdapter, result: generic, limited: false };
  }
  return null;
}
