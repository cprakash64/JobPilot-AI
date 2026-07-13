/**
 * ATS adapter registry + deterministic detection. Specific adapters are tried
 * first; the generic HTML-form adapter is the fallback. Workday is detected only
 * to warn the user of limited support in this phase (no dedicated adapter yet).
 */

import type { ATSAdapter, DetectionResult, PageContext } from "../types";
import { AshbyAdapter } from "./ashby";
import { hostMatches } from "./base";
import { GenericFormAdapter } from "./generic";
import { GreenhouseAdapter } from "./greenhouse";
import { LeverAdapter } from "./lever";

export const SPECIFIC_ADAPTERS: ATSAdapter[] = [GreenhouseAdapter, LeverAdapter, AshbyAdapter];
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

  // Workday: detect and fall back to the generic adapter with a "limited" flag.
  if (hostMatches(context.url, "myworkdayjobs.com") || hostMatches(context.url, "workday.com")) {
    return { adapter: GenericFormAdapter, result: { atsId: "workday", matched: true, confidence: 0.4 }, limited: true };
  }

  const generic = GenericFormAdapter.detect(context);
  if (generic.matched) {
    return { adapter: GenericFormAdapter, result: generic, limited: false };
  }
  return null;
}
