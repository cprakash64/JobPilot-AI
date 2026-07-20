/**
 * Browser test harness. Bundled to `e2e/bundle/harness.js` and injected into a
 * real Chromium page by the Playwright specs, so the dropdown adapters are
 * exercised against genuine layout, real pointer/focus behaviour, real portals
 * and real component JS — the things jsdom cannot model.
 *
 * Test-only entry point: it is NOT part of the shipped extension bundle.
 */

import { discoverAll } from "./fields/discovery";
import { fillField } from "./fields/fill";
import { configureDropdownTiming, isBlankValue } from "./fields/dropdown/dom";
import { probeFrame, selectApplicationFrame } from "./frames/probe";
import { dropdownEventLog, fillDropdown, selectAdapter } from "./fields/dropdown";
import { valuePresent } from "./fields/ledger";
import type { DiscoveredField } from "./types";

let cache: DiscoveredField[] = [];

function discover(selector = "form"): DiscoveredField[] {
  const root = document.querySelector(selector) ?? document;
  cache = discoverAll(root).fields;
  return cache;
}

function find(idOrLabel: string): DiscoveredField | undefined {
  return (
    cache.find((f) => f.id === idOrLabel) ??
    cache.find((f) => (f.label || f.ariaLabel || "").toLowerCase().includes(idOrLabel.toLowerCase()))
  );
}

const harness = {
  configureDropdownTiming,
  discover: (selector?: string) =>
    discover(selector).map((f) => ({
      uid: f.uid,
      id: f.id,
      label: f.label || f.ariaLabel,
      control: f.control,
      required: f.required,
      multiple: f.multiple,
      options: f.options,
      adapter: selectAdapter(f)?.id ?? null
    })),

  /** Drive one dropdown exactly as automatic autofill does. */
  fill: async (idOrLabel: string, value: string | string[]) => {
    const field = find(idOrLabel);
    if (!field) return { error: "field-not-found" };
    const outcome = await fillField(field, value, { status: "verified", force: true });
    return { status: outcome.status, reason: outcome.reason, dropdown: outcome.dropdown };
  },

  /** Open a dropdown only to read its real options (no answer supplied).
   * This is the ONE legitimate opt-in for allowProbe: an explicit request to
   * enumerate options, never part of an autofill run. */
  probe: async (idOrLabel: string) => {
    const field = find(idOrLabel);
    if (!field) return { error: "field-not-found" };
    const result = await fillDropdown(field, { values: [], allowProbe: true });
    return { reason: result.reason, options: result.options };
  },

  /** What the control actually shows as selected, per the adapter. */
  selection: (idOrLabel: string) => {
    const field = find(idOrLabel);
    if (!field) return null;
    return selectAdapter(field)?.readSelection(field) ?? null;
  },

  /** Completeness: is this control genuinely non-blank? */
  hasValue: (idOrLabel: string) => {
    const field = find(idOrLabel);
    return field ? valuePresent(field) : null;
  },

  isBlankValue,
  events: () => dropdownEventLog(),

  /** Section B — per-frame application census, run inside a real frame. */
  probeFrame: () => probeFrame(document),
  /** Section B — rank probes across frames and pick the application frame. */
  selectApplicationFrame: (probes: Parameters<typeof selectApplicationFrame>[0]) =>
    selectApplicationFrame(probes)
};

(window as unknown as { JobPilotHarness: typeof harness }).JobPilotHarness = harness;
export type Harness = typeof harness;
