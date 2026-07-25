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
import { createWidget } from "./content/widget";
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

  /** Visual fixture for the assisted-apply panel. Keeps the production widget
   * itself under browser-level layout review without shipping preview code. */
  showWidgetPreview: () => {
    const attachShadow = Element.prototype.attachShadow;
    let previewRoot: ShadowRoot | null = null;
    Element.prototype.attachShadow = function (init: ShadowRootInit) {
      previewRoot = attachShadow.call(this, { ...init, mode: "open" });
      return previewRoot;
    };
    const widget = createWidget({ retry: () => {}, clear: () => {}, complete: () => {} });
    Element.prototype.attachShadow = attachShadow;
    widget.update({
      stage: "review",
      filled: 20,
      total: 26,
      message: "5 items need your review (2 required)."
    });
    widget.showReview([
      {
        id: "school", kind: "field", category: "application",
        question: "Which school, college, or university did you attend?",
        required: false,
        reasonText: "Enter the institution name exactly as you want it shown on this application.",
        control: "text", reusable: true, defaultScope: "global"
      },
      {
        id: "degree", kind: "field", category: "required",
        question: "What type of degree did you earn?",
        required: true,
        reasonText: "Choose the closest degree level offered by the employer (for example, Master's Degree for a Master of Science).",
        options: ["Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctoral Degree"],
        control: "select", reusable: true, defaultScope: "global"
      },
      {
        id: "major", kind: "field", category: "application",
        question: "What was your major or field of study?",
        required: false,
        reasonText: "Enter the subject you studied, sometimes called your discipline or field of study.",
        control: "text", reusable: true, defaultScope: "global"
      }
    ], {
      onFill: async () => true,
      onSave: async () => true,
      onJumpToField: () => {}
    }, {
      discovered: 26, filled: 20, needsInformation: 3, needsConfirmation: 1,
      sensitive: 1, technical: 0, optionalSkipped: 1, requiredBlank: 2, pending: 5
    });
    (previewRoot as ShadowRoot | null)?.querySelector<HTMLButtonElement>(".review-toggle")?.click();
    return true;
  },

  /** Section B — per-frame application census, run inside a real frame. */
  probeFrame: () => probeFrame(document),
  /** Section B — rank probes across frames and pick the application frame. */
  selectApplicationFrame: (probes: Parameters<typeof selectApplicationFrame>[0]) =>
    selectApplicationFrame(probes)
};

(window as unknown as { JobPilotHarness: typeof harness }).JobPilotHarness = harness;
export type Harness = typeof harness;
