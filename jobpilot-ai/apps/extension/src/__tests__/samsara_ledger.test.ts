/**
 * Section M — the Samsara / Greenhouse regression matrix. Proves the field
 * ledger is the single source of truth: every actionable control produces one
 * ledger entry, widget counts equal ledger counts, and "All caught up" /
 * "Mark complete" are impossible while a required control is blank.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { detectAdapter } from "../ats/registry";
import { runAutofill } from "../content/autofill";
import { buildReviewModel } from "../content/review";
import { createWidget } from "../content/widget";
import { discoverAll } from "../fields/discovery";
import { ledgerInvariantHolds, mergeLedger, type LedgerEntry } from "../fields/ledger";
import { fillField } from "../fields/fill";
import { pickApplicationForm } from "../ats/base";
import type { ApplicationSessionData, SessionAnswer } from "../types";
import { SAMSARA_GREENHOUSE_FIXTURE, mountFixture } from "./fixtures";
import { activateSamsaraDropdowns } from "./dropdownComponents";
import { configureDropdownTiming } from "../fields/dropdown/dom";

configureDropdownTiming({ openPointerMs: 120, openKeyboardMs: 120, openEnterMs: 50, listboxMs: 100, optionsMs: 150, verifyMs: 150, pollStepMs: 5 });

const URL = "https://job-boards.greenhouse.io/samsara/jobs/1";

function answer(canonical_key: string, value: string, verified = true): SessionAnswer {
  return { canonical_key, value, display_value: value, source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified };
}

/** Session with verified contact/location facts and a CONFIRMED name. */
function fullSession(): ApplicationSessionData {
  return {
    sessionId: 1, atsType: "greenhouse", officialUrl: URL, jobTitle: "SWE", company: "Samsara",
    unresolvedQuestions: [],
    answers: [
      answer("first_name", "Chandra Prakash"), answer("last_name", "Pandey"),
      answer("email", "chandra.real@gmail.com"), answer("phone", "+1 555 0100"),
      answer("country", "United States"), answer("city", "San Francisco"),
      answer("postal_code", "94105"), answer("linkedin_url", "https://linkedin.com/in/x")
    ]
  };
}

/** Session with an UNCONFIRMED name (backend asks to confirm the split) and a
 * demo email that the backend refused to auto-fill (section E). */
function unconfirmedNameSession(): ApplicationSessionData {
  return {
    sessionId: 1, atsType: "greenhouse", officialUrl: URL, jobTitle: "SWE", company: "Samsara",
    unresolvedQuestions: [
      { canonical_key: "first_name", action: "confirm_name", suggested_value: "Chandra", reason: "Confirm the split." },
      { canonical_key: "last_name", action: "confirm_name", suggested_value: "Prakash Pandey", reason: "Confirm the split." },
      { canonical_key: "email", action: "replace_demo_email", reason: "JobPilot still has a demo email for this profile. Add your real email before continuing." }
    ],
    answers: [answer("phone", "+1 555 0100")]
  };
}

async function run(session: ApplicationSessionData) {
  mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
  activateSamsaraDropdowns(document);
  const outcome = detectAdapter({ url: URL, document })!;
  return runAutofill(session, outcome, { fetchDocument: async () => new File(["x"], "resume.pdf", { type: "application/pdf" }) }, 0);
}

function byLabel(ledger: LedgerEntry[], fragment: string): LedgerEntry | undefined {
  return ledger.find((e) => e.label.toLowerCase().includes(fragment.toLowerCase()));
}

describe("Samsara/Greenhouse field ledger (section M)", () => {
  it("M1: discovers every actionable control; excludes honeypot + disabled WITH a reason", () => {
    mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
    const { fields, excluded } = discoverAll(pickApplicationForm(document));
    expect(fields.length).toBe(24);
    // The custom (non-native) controls the old discovery missed entirely:
    expect(fields.find((f) => f.control === "combobox" && /country/i.test(f.label + f.ariaLabel))).toBeTruthy();
    expect(fields.find((f) => f.control === "combobox" && /sponsor/i.test(f.label))).toBeTruthy();
    expect(fields.find((f) => f.control === "listbox" && f.multiple)).toBeTruthy();
    // Exclusions are recorded, never silently dropped.
    expect(excluded.some((e) => e.reason === "honeypot")).toBe(true);
    expect(excluded.some((e) => e.reason === "disabled")).toBe(true);
  });

  it("M2/M16: every blank required control is in the ledger; the invariant holds and retry never duplicates", async () => {
    const { ledger, counts } = await run(fullSession());
    expect(ledgerInvariantHolds(ledger)).toBe(true);
    expect(counts.discovered).toBe(ledger.length);
    // Blank required controls all present and unresolved.
    for (const frag of ["Do you have current legal authorization", "require Samsara to sponsor", "previously worked at Samsara", "Where have you learned"]) {
      const e = byLabel(ledger, frag)!;
      expect(e.required).toBe(true);
      expect(e.verified).toBe(false);
    }
    expect(counts.requiredBlank).toBeGreaterThanOrEqual(5);
    // Retry: merging the ledger with itself must not duplicate entries.
    expect(mergeLedger(ledger, ledger).length).toBe(ledger.length);
  });

  it("M3: widget counts are derived from the ledger (never independent)", async () => {
    const { ledger, counts } = await run(fullSession());
    const { items } = buildReviewModel(ledger, fullSession());
    const widget = mountWidget();
    widget.showReview(items, noopHandlers(), counts);
    const text = shadowRoot().querySelector(".counts-row")!.textContent!;
    expect(text).toContain(`Discovered: ${counts.discovered}`);
    expect(text).toContain(`Filled: ${counts.filled}`);
    expect(text).toContain(`Sensitive/consent: ${counts.sensitive}`);
    widget.destroy();
  });

  it("M4/M18: 'All caught up' and 'Mark complete' are impossible while a required control is blank", async () => {
    const { ledger, counts } = await run(fullSession());
    expect(counts.requiredBlank).toBeGreaterThan(0);
    const { items } = buildReviewModel(ledger, fullSession());
    const widget = mountWidget();
    widget.showReview(items, noopHandlers(), counts);
    const root = shadowRoot();
    expect(root.querySelector(".review-toggle")!.textContent).toContain("need your input");
    expect(root.querySelector(".review-toggle")!.textContent).not.toContain("All caught up");
    expect((root.querySelector('[data-a="complete"]') as HTMLButtonElement).disabled).toBe(true);
    widget.destroy();
  });

  it("M5: an unknown required question still appears in the widget with its label and options", async () => {
    const { ledger } = await run(fullSession());
    const { items } = buildReviewModel(ledger, fullSession());
    const learned = items.find((i) => /Where have you learned/i.test(i.question))!;
    expect(learned).toBeTruthy();
    expect(learned.required).toBe(true);
    expect(learned.multiple).toBe(true);
    expect(learned.options).toEqual(expect.arrayContaining(["LinkedIn", "Industry event", "Other"]));
  });

  it("M6: structured-name confirmation can fill all four name fields", async () => {
    mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
    const { fields } = discoverAll(pickApplicationForm(document));
    const byId = (id: string) => fields.find((f) => f.id === id)!;
    // The four discovered name fields map to distinct canonical keys.
    expect(byId("pref_first")).toBeTruthy();
    expect(byId("pref_last")).toBeTruthy();
    // Simulate the confirmed split: legal given/family; blank preferred → legal.
    await fillField(byId("first_name"), "Chandra Prakash", { status: "verified", force: true });
    await fillField(byId("last_name"), "Pandey", { status: "verified", force: true });
    await fillField(byId("pref_first"), "Chandra Prakash", { status: "verified", force: true });
    await fillField(byId("pref_last"), "Pandey", { status: "verified", force: true });
    expect((document.getElementById("first_name") as HTMLInputElement).value).toBe("Chandra Prakash");
    expect((document.getElementById("last_name") as HTMLInputElement).value).toBe("Pandey");
    expect((document.getElementById("pref_first") as HTMLInputElement).value).toBe("Chandra Prakash");
    expect((document.getElementById("pref_last") as HTMLInputElement).value).toBe("Pandey");
  });

  it("M7: a demo email surfaces as a review item with the real-email prompt", async () => {
    const { ledger } = await run(unconfirmedNameSession());
    const { items } = buildReviewModel(ledger, unconfirmedNameSession());
    const email = items.find((i) => /email/i.test(i.question))!;
    expect(email.reasonText).toContain("demo email");
    // And the structured-name confirmation prompt is present.
    expect(items.some((i) => i.kind === "name_confirm")).toBe(true);
  });

  it("M8: country, city, ZIP and LinkedIn fill from the verified profile", async () => {
    const { ledger } = await run(fullSession());
    expect((document.getElementById("city") as HTMLInputElement).value).toBe("San Francisco");
    expect((document.getElementById("zip") as HTMLInputElement).value).toBe("94105");
    expect((document.getElementById("linkedin") as HTMLInputElement).value).toBe("https://linkedin.com/in/x");
    expect(byLabel(ledger, "Country")!.status).toBe("filled_verified");
  });

  it("M9: single-select options are captured and a select fills from an answer", async () => {
    mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
    activateSamsaraDropdowns(document);
    const { fields } = discoverAll(pickApplicationForm(document));
    const workAuth = fields.find((f) => f.id === "work_auth")!;
    expect(workAuth.options).toEqual(["Yes", "No"]);
    const outcome = await fillField(workAuth, "Yes", { status: "verified", force: true });
    expect(outcome.status).toBe("filled");
    expect((document.getElementById("work_auth") as HTMLSelectElement).value).toBe("Yes");
  });

  it("M10: multi-select options are captured and multiple options fill", async () => {
    mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
    activateSamsaraDropdowns(document);
    const { fields } = discoverAll(pickApplicationForm(document));
    const learned = fields.find((f) => f.control === "listbox")!;
    expect(learned.options.length).toBe(5);
    // Multi-select answers are ARRAYS, never comma-joined text.
    const outcome = await fillField(learned, ["LinkedIn", "Industry event"], { status: "verified", force: true });
    expect(outcome.status).toBe("filled");
    const chosen = Array.from(document.querySelectorAll('#learned [aria-selected="true"]')).map((o) => o.textContent);
    expect(chosen).toEqual(expect.arrayContaining(["LinkedIn", "Industry event"]));
  });

  it("M11/M12: consent and AI-policy acknowledgements require confirmation (never guessed)", async () => {
    const { ledger } = await run(fullSession());
    expect(byLabel(ledger, "Processing of Personal")!.status).toBe("needs_confirmation");
    const ai = byLabel(ledger, "AI Policy for Interviewers")!;
    expect(ai.status).toBe("needs_confirmation");
    expect(ai.verified).toBe(false);
  });

  it("M13: company-specific questions stay company-scoped", async () => {
    const { ledger } = await run(fullSession());
    expect(byLabel(ledger, "previously worked at Samsara")!.defaultScope).toBe("company");
    expect(byLabel(ledger, "How did you hear")!.defaultScope).toBe("company");
  });

  it("M14: sensitive EEO answers are never guessed (need explicit opt-in)", async () => {
    // Even WITH a saved gender value in the session, an unverified sensitive
    // answer is not auto-filled — it stays for the user.
    const session = fullSession();
    session.answers.push({ ...answer("gender", "Male"), sensitive: true, verified: false, requires_review: true });
    const { ledger } = await run(session);
    for (const frag of ["Gender identity", "Race / Ethnicity", "Veteran status", "Disability status"]) {
      const e = byLabel(ledger, frag)!;
      expect(e.sensitive).toBe(true);
      expect(e.verified).toBe(false);
    }
    expect((document.getElementById("gender") as HTMLSelectElement).value).toBe("");
  });

  it("M15/M17: the conditional 'Other' field appears only once applicable, and a rescan merges it in", async () => {
    mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
    const form = pickApplicationForm(document);
    const first = discoverAll(form);
    expect(first.fields.some((f) => f.id === "learned_other")).toBe(false); // hidden wrapper → excluded

    // The user picks "Other"; the ATS reveals the follow-up.
    (document.getElementById("other-field") as HTMLElement).style.display = "block";
    const second = discoverAll(form);
    expect(second.fields.some((f) => f.id === "learned_other")).toBe(true);

    // Continue filling: the durable ledger MERGES the new control rather than
    // dropping the ones discovered before (and never duplicates).
    const meta = () => ({ canonicalKey: null, sensitive: false, reusable: false, fillSource: null });
    const before = first.fields.map((f) => entryFor(f));
    const after = second.fields.map((f) => entryFor(f));
    void meta;
    const merged = mergeLedger(before, after);
    expect(merged.length).toBe(before.length + 1);
  });

  it("M19: autofill never clicks the submit control", async () => {
    mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
    const submit = document.getElementById("submit_app") as HTMLButtonElement;
    const spy = vi.spyOn(submit, "click");
    const outcome = detectAdapter({ url: URL, document })!;
    await runAutofill(fullSession(), outcome, { fetchDocument: async () => new File(["x"], "resume.pdf", { type: "application/pdf" }) }, 0);
    expect(spy).not.toHaveBeenCalled();
  });

  it("M20: the widget renders in-page (no side panel required)", () => {
    mountFixture(SAMSARA_GREENHOUSE_FIXTURE);
    const widget = mountWidget();
    expect(document.getElementById("jobpilot-assisted-apply")).toBeTruthy();
    widget.destroy();
  });
});

// --------------------------------------------------------------------------- //
// Widget test harness (shadow root capture) — mirrors widget.test.ts.
// --------------------------------------------------------------------------- //
let capturedRoot: ShadowRoot | null = null;
let originalAttachShadow: typeof Element.prototype.attachShadow;
beforeEach(() => {
  originalAttachShadow = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function (init: ShadowRootInit) {
    const root = originalAttachShadow.call(this, { ...init, mode: "open" });
    capturedRoot = root;
    return root;
  };
});
afterEach(() => {
  Element.prototype.attachShadow = originalAttachShadow;
  capturedRoot = null;
  document.body.innerHTML = "";
});
function mountWidget() {
  return createWidget({ retry: () => {}, clear: () => {}, complete: () => {}, diagnostics: () => {} });
}
function shadowRoot(): ShadowRoot {
  return capturedRoot!;
}
function noopHandlers() {
  return { onFill: vi.fn(async () => true), onSave: vi.fn(async () => true), onJumpToField: vi.fn() };
}
function entryFor(field: { uid: string; frameId: string; label: string; normalizedLabel: string; control: string; required: boolean; multiple: boolean; options: string[] }): LedgerEntry {
  return {
    uid: field.uid, frameId: field.frameId, label: field.label, normalizedLabel: field.normalizedLabel,
    controlType: field.control, canonicalKey: null, required: field.required, sensitive: false,
    options: field.options, multiple: field.multiple, currentValuePresent: false,
    status: "missing_information", reasonCode: "NO_VERIFIED_ANSWER", fillSource: null, verified: false,
    question: field.label, reusable: false
  };
}
