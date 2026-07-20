/**
 * Sections D/E/F/K — "Teach JobPilot".
 *
 * JobPilot observes only TRUSTED user interactions inside the verified
 * application root, reads the committed value through the same dropdown adapter
 * used for autofill, and never persists anything without an explicit user choice.
 */
import { describe, expect, it } from "vitest";
import { discoverAll } from "../fields/discovery";
import { configureDropdownTiming } from "../fields/dropdown/dom";
import { fillField } from "../fields/fill";
import { proposeScope, readCommitted, startTeachMode, fieldFingerprint, type LearnedAnswer } from "../content/teach";
import { attachDropdownComponent } from "./dropdownComponents";
import type { DiscoveredField } from "../types";

configureDropdownTiming({ openPointerMs: 120, openKeyboardMs: 120, openEnterMs: 50, listboxMs: 100, optionsMs: 150, verifyMs: 150, pollStepMs: 5 });

const settle = () => new Promise((r) => setTimeout(r, 400));

/** Simulate a user interaction. jsdom makes `isTrusted` non-configurable, so it
 * cannot be forged; the learning tests therefore run the observer with
 * `requireTrusted: false`. The guard itself is proved by the dedicated
 * "IGNORES JobPilot's own autofill" test below, which runs with the production
 * default and feeds it genuinely untrusted events. */
function userEvent(el: Element, type: string, ctor: typeof Event | typeof MouseEvent = Event): void {
  el.dispatchEvent(new ctor(type, { bubbles: true }));
}

function mountForm(): { root: HTMLElement; fields: Map<string, DiscoveredField> } {
  document.body.innerHTML = `
    <form id="app">
      <div class="field"><label for="city">Location (City)</label><input id="city" name="city" type="text" /></div>
      <div class="field"><label for="prev">Have you previously worked at Samsara?</label>
        <select id="prev" name="prev"><option value="">Select...</option><option>Yes</option><option>No</option></select></div>
      <div class="field">
        <span id="src-label">Where did you hear about us?</span>
        <div id="src" class="select__control" role="combobox" aria-labelledby="src-label"
             aria-expanded="false" aria-controls="src-menu"><span class="select__value"></span></div>
        <ul id="src-menu" role="listbox"></ul>
      </div>
      <div class="field">
        <span id="multi-label">Which channels? (select all)</span>
        <div id="multi" class="select__control" role="combobox" aria-labelledby="multi-label"
             aria-multiselectable="true" aria-expanded="false" aria-controls="multi-menu"><span class="select__value"></span></div>
        <ul id="multi-menu" role="listbox" aria-multiselectable="true"></ul>
      </div>
    </form>`;
  const src = document.getElementById("src")!;
  attachDropdownComponent({
    control: src, menu: document.getElementById("src-menu")!,
    options: ["LinkedIn", "Employee referral"], display: src.querySelector<HTMLElement>(".select__value")!
  });
  const multi = document.getElementById("multi")!;
  attachDropdownComponent({
    control: multi, menu: document.getElementById("multi-menu")!, multiple: true,
    options: ["Email", "Event", "Podcast"], display: multi.querySelector<HTMLElement>(".select__value")!
  });
  const root = document.getElementById("app") as HTMLElement;
  const discovered = discoverAll(root).fields;
  return { root, fields: new Map(discovered.map((f) => [f.uid, f])) };
}

function teach(root: HTMLElement, fields: Map<string, DiscoveredField>, over: Partial<Parameters<typeof startTeachMode>[0]> = {}) {
  const learned: LearnedAnswer[] = [];
  const stop = startTeachMode({
    root,
    fields: () => fields,
    ats: "greenhouse",
    employer: "Samsara",
    canonicalKeyFor: (uid) => ({} as Record<string, string>)[uid] ?? null,
    sensitiveFor: () => false,
    onLearned: (a) => learned.push(a),
    requireTrusted: false,
    ...over
  });
  return { learned, stop };
}

describe("Teach JobPilot", () => {
  it("learns a manually typed text answer", async () => {
    const { root, fields } = mountForm();
    const { learned, stop } = teach(root, fields);
    const city = document.getElementById("city") as HTMLInputElement;
    city.value = "San Francisco";
    userEvent(city, "change");
    await settle();
    stop();
    expect(learned).toHaveLength(1);
    expect(learned[0].chosen).toEqual(["San Francisco"]);
    expect(learned[0].question).toMatch(/Location \(City\)/);
  });

  it("learns a native select choice, never the placeholder", async () => {
    const { root, fields } = mountForm();
    const { learned, stop } = teach(root, fields);
    const prev = document.getElementById("prev") as HTMLSelectElement;
    prev.value = "No";
    userEvent(prev, "change");
    await settle();
    stop();
    expect(learned.map((l) => l.chosen)).toEqual([["No"]]);
    // The placeholder was never emitted as an answer.
    expect(JSON.stringify(learned)).not.toContain("Select...");
  });

  it("learns a CUSTOM dropdown selection via the adapter's readSelection", async () => {
    const { root, fields } = mountForm();
    const { learned, stop } = teach(root, fields);
    // The user really clicks the control and an option.
    const control = document.getElementById("src")!;
    userEvent(control, "mousedown", MouseEvent);
    const option = Array.from(document.querySelectorAll("#src-menu [role=option]")).find((o) => o.textContent === "Employee referral")!;
    userEvent(option, "click", MouseEvent);
    await settle();
    stop();
    const entry = learned.find((l) => l.question.includes("Where did you hear"))!;
    expect(entry.chosen).toEqual(["Employee referral"]);
    expect(entry.options).toBeDefined();
  });

  it("learns a MULTI-select as an array, not comma-joined text", async () => {
    const { root, fields } = mountForm();
    const { learned, stop } = teach(root, fields);
    const control = document.getElementById("multi")!;
    userEvent(control, "mousedown", MouseEvent);
    for (const label of ["Email", "Podcast"]) {
      const option = Array.from(document.querySelectorAll("#multi-menu [role=option]")).find((o) => o.textContent === label)!;
      userEvent(option, "click", MouseEvent);
    }
    await settle();
    stop();
    const entry = learned.filter((l) => l.question.includes("Which channels")).pop()!;
    expect(entry.chosen).toEqual(["Email", "Podcast"]);
    expect(Array.isArray(entry.chosen)).toBe(true);
  });

  it("IGNORES JobPilot's own autofill — only trusted user input teaches", async () => {
    const { root, fields } = mountForm();
    // Production default: requireTrusted === true.
    const { learned, stop } = teach(root, fields, { requireTrusted: true });
    // fillField dispatches synthetic (isTrusted === false) events.
    const city = Array.from(fields.values()).find((f) => f.id === "city")!;
    await fillField(city, "Autofilled City", { force: true });
    await settle();
    stop();
    expect(learned).toHaveLength(0);
  });

  it("ignores interactions outside the application root", async () => {
    const { root, fields } = mountForm();
    const outside = document.createElement("input");
    outside.id = "site-search";
    document.body.appendChild(outside);
    const { learned, stop } = teach(root, fields);
    outside.value = "search text";
    userEvent(outside, "change");
    await settle();
    stop();
    expect(learned).toHaveLength(0);
  });

  it("never emits an answer for a cleared field", async () => {
    const { root, fields } = mountForm();
    const city = document.getElementById("city") as HTMLInputElement;
    city.value = "San Francisco";
    const { learned, stop } = teach(root, fields); // baseline includes the value
    city.value = "";
    userEvent(city, "change");
    await settle();
    stop();
    expect(learned).toHaveLength(0);
  });

  it("stops observing after stop()", async () => {
    const { root, fields } = mountForm();
    const { learned, stop } = teach(root, fields);
    stop();
    const city = document.getElementById("city") as HTMLInputElement;
    city.value = "Berlin";
    userEvent(city, "change");
    await settle();
    expect(learned).toHaveLength(0);
  });
});

describe("learned-answer scope proposal", () => {
  it("proposes global for stable profile facts", () => {
    for (const key of ["country", "city", "postal_code", "pronouns", "work_authorization_us", "sponsorship_required_future"]) {
      expect(proposeScope(key, false)).toEqual({ scope: "global", confident: true });
    }
  });

  it("proposes company scope for employer-specific history", () => {
    for (const key of ["previously_employed", "referral_source", "relatives_employed", "previously_interviewed"]) {
      expect(proposeScope(key, false)).toEqual({ scope: "company", confident: true });
    }
  });

  it("proposes application-only for consent and role-specific answers", () => {
    for (const key of ["legal_attestation", "custom_motivation", "salary_expectation"]) {
      expect(proposeScope(key, false)).toEqual({ scope: "application", confident: true });
    }
  });

  it("proposes sensitive scope for EEO categories", () => {
    expect(proposeScope("gender", true)).toEqual({ scope: "sensitive", confident: true });
    expect(proposeScope("race", true)).toEqual({ scope: "sensitive", confident: true });
  });

  it("never guesses a scope for an unknown question — asks instead", () => {
    const result = proposeScope(null, false);
    expect(result.scope).toBe("application"); // safest default
    expect(result.confident).toBe(false); // widget MUST ask
  });
});

describe("field fingerprinting", () => {
  it("is stable for the same question across applications at one ATS", () => {
    const { fields } = mountForm();
    const city = Array.from(fields.values()).find((f) => f.id === "city")!;
    const first = fieldFingerprint(city, "greenhouse");

    const remounted = mountForm();
    const cityAgain = Array.from(remounted.fields.values()).find((f) => f.id === "city")!;
    expect(fieldFingerprint(cityAgain, "greenhouse")).toBe(first);
  });

  it("readCommitted returns [] for a placeholder-only dropdown", () => {
    const { fields } = mountForm();
    const src = Array.from(fields.values()).find((f) => f.id === "src")!;
    expect(readCommitted(src)).toEqual([]);
  });
});
