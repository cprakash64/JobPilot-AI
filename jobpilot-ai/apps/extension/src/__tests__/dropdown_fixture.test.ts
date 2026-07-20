/**
 * Manual fixture test (task section L). An Affirm/Greenhouse-style form
 * covering the full dropdown surface: native selects (pronouns, current
 * sponsorship, state/province, referral source, previously-employed,
 * demographic EEO selects) and one ARIA combobox (future sponsorship — kept
 * logically distinct from "current sponsorship", never collapsed into one
 * answer). Verifies the compound given name lands entirely in First Name,
 * known dropdowns are selected without ever picking a placeholder/first
 * option, unresolved dropdowns surface their real discovered options for the
 * review widget, and the application is never submitted.
 */

import { describe, expect, it } from "vitest";
import { detectAdapter } from "../ats/registry";
import { runAutofill } from "../content/autofill";
import type { ApplicationSessionData } from "../types";
import { DROPDOWN_FIXTURE, mountFixture } from "./fixtures";
import { attachDropdownComponent } from "./dropdownComponents";
import { configureDropdownTiming } from "../fields/dropdown/dom";

configureDropdownTiming({ openPointerMs: 120, openKeyboardMs: 120, openEnterMs: 50, listboxMs: 100, optionsMs: 150, verifyMs: 150, pollStepMs: 5 });

/** The fixture's future-sponsorship ARIA combobox, wired with real component
 * behaviour so a selection only counts when the component actually commits it. */
function activateSponsorCombobox(delayMs?: number): void {
  const input = document.getElementById("sponsorFuture") as HTMLInputElement;
  attachDropdownComponent({
    control: input,
    menu: document.getElementById("sponsorFuture-listbox")!,
    options: ["Yes, I will require sponsorship", "No, I will not require sponsorship"],
    valueInput: input,
    openDelayMs: delayMs
  });
}

function session(): ApplicationSessionData {
  const a = (canonical_key: string, value: string, extra: Partial<ApplicationSessionData["answers"][number]> = {}) => ({
    canonical_key, value, display_value: value, source: "profile", confidence: 0.97,
    sensitive: false, requires_review: false, verified: true, ...extra
  });
  return {
    sessionId: 7,
    atsType: null,
    officialUrl: "https://boards.greenhouse.io/affirm/jobs/1",
    jobTitle: "Backend Engineer",
    company: "Affirm",
    unresolvedQuestions: [],
    answers: [
      // Confirmed structured name — arrives pre-split from the backend, the
      // extension never splits full_name itself.
      a("first_name", "Chandra Prakash"),
      a("last_name", "Pandey"),
      a("email", "chandra.pandey@realmail.example"),
      a("phone", "+1 415 555 0100"),
      a("country", "United States"),
      a("state", "Arizona"), // exercises the state<->AZ alias
      a("linkedin_url", "https://linkedin.com/in/cp"),
      a("current_company", "Acme Corp"),
      a("pronouns", "They/them"),
      a("sponsorship_required_now", "Yes"),
      a("sponsorship_required_future", "Yes"),
      a("work_authorization_us", "authorized_us")
      // referral_source intentionally absent → the employer careers-page
      // default applies. previously_employed remains a review item.
    ]
  };
}

describe("manual dropdown fixture (task section L)", () => {
  it("places the compound given name entirely in First Name and the family name in Last Name", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    activateSponsorCombobox();
    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    await runAutofill(session(), outcome, { fetchDocument: async () => null });

    expect((document.getElementById("firstName") as HTMLInputElement).value).toBe("Chandra Prakash");
    expect((document.getElementById("lastName") as HTMLInputElement).value).toBe("Pandey");
  });

  it("fills native selects, the ARIA combobox, and keeps current vs future sponsorship distinct", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    // The combobox's options render asynchronously, mirroring a real ATS.
    activateSponsorCombobox(30);

    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    const res = await runAutofill(session(), outcome, { fetchDocument: async () => null });

    expect((document.getElementById("country") as HTMLSelectElement).value).toBe("US");
    expect((document.getElementById("state") as HTMLSelectElement).value).toBe("AZ");
    expect((document.getElementById("pronouns") as HTMLSelectElement).value).toBe("They/them");
    expect((document.getElementById("sponsorNow") as HTMLSelectElement).value).toBe("Yes");
    // Future sponsorship went through the combobox path, not the native select.
    expect((document.getElementById("sponsorFuture") as HTMLInputElement).value).toContain("Yes");
    // The two sponsorship questions were filled independently — this is only
    // meaningful because the session provided both a "now" and "future"
    // answer and both controls actually received a value (not the same
    // control filled twice, not one left blank).
    expect(res.fieldResults.find((r) => r.fieldKey === "sponsorship_required_now")?.status).toBe("filled");
    expect(res.fieldResults.find((r) => r.fieldKey === "sponsorship_required_future")?.status).toBe("filled");
  });

  it("defaults referral source to the employer careers page and still surfaces unanswered factual questions", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    const res = await runAutofill(session(), outcome, { fetchDocument: async () => null });

    const referral = res.fieldResults.find((r) => r.fieldKey === "referral_source");
    expect(referral?.status).toBe("filled");
    expect(referral?.reusable).toBe(true);
    expect(referral?.defaultScope).toBe("company"); // never auto-reused across employers

    const prevEmployed = res.fieldResults.find((r) => r.fieldKey === "previously_employed");
    expect(prevEmployed?.status).toBe("review");
    expect(prevEmployed?.options).toEqual(expect.arrayContaining(["Yes", "No"]));

    expect((document.getElementById("referral") as HTMLSelectElement).value).toBe("Company website");
    expect((document.getElementById("prevEmployed") as HTMLSelectElement).value).toBe("");
  });

  it("uses an explicit referral answer instead of the careers-page default", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    const explicitReferral = session();
    explicitReferral.answers.push({
      canonical_key: "referral_source", value: "Employee referral", display_value: "Employee referral",
      source: "answer_vault", confidence: 1, sensitive: false, requires_review: false, verified: true
    });
    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    await runAutofill(explicitReferral, outcome, { fetchDocument: async () => null });

    expect((document.getElementById("referral") as HTMLSelectElement).value).toBe("Employee referral");
  });

  it("acknowledges a privacy policy and the required single-option AI-use attestation", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    const res = await runAutofill(session(), outcome, { fetchDocument: async () => null });

    expect((document.getElementById("privacyPolicy") as HTMLSelectElement).value).toContain("I acknowledge");
    expect(res.fieldResults.find((r) => r.fieldKey === "privacy_policy_acknowledgement")?.status).toBe("filled");
    expect((document.getElementById("aiAttestation") as HTMLSelectElement).value).toBe("I agree that all submitted materials are my original work and were completed without AI tools.");
    expect(res.fieldResults.find((r) => r.fieldKey === "legal_attestation")?.status).toBe("filled");
  });

  it("never selects a demographic (EEO) dropdown automatically", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    const res = await runAutofill(session(), outcome, { fetchDocument: async () => null });

    expect((document.getElementById("gender") as HTMLSelectElement).value).toBe("");
    expect((document.getElementById("veteran") as HTMLSelectElement).value).toBe("");
    expect((document.getElementById("race") as HTMLSelectElement).value).toBe("");
    for (const key of ["gender", "veteran_status", "race"]) {
      const r = res.fieldResults.find((f) => f.fieldKey === key);
      expect(r?.reasonCode).toBe("SENSITIVE_FIELD");
    }
  });

  it("fills EEO dropdowns only from explicitly consented and verified session answers", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    const withEeo = session();
    withEeo.answers.push(
      { canonical_key: "gender", value: "Man", display_value: "Man", source: "profile_eeo", confidence: 1, sensitive: true, requires_review: false, verified: true },
      { canonical_key: "veteran_status", value: "I am not a veteran", display_value: "I am not a veteran", source: "profile_eeo", confidence: 1, sensitive: true, requires_review: false, verified: true },
      { canonical_key: "race", value: "Asian", display_value: "Asian", source: "profile_eeo", confidence: 1, sensitive: true, requires_review: false, verified: true }
    );
    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    const res = await runAutofill(withEeo, outcome, { fetchDocument: async () => null });

    expect((document.getElementById("gender") as HTMLSelectElement).value).toBe("Male");
    expect((document.getElementById("veteran") as HTMLSelectElement).value).toBe("I am not a veteran");
    expect((document.getElementById("race") as HTMLSelectElement).value).toBe("Asian");
    for (const key of ["gender", "veteran_status", "race"]) {
      expect(res.fieldResults.find((field) => field.fieldKey === key)?.status).toBe("filled");
    }
  });

  it("never clicks Submit", async () => {
    mountFixture(DROPDOWN_FIXTURE);
    const submit = document.getElementById("submit_app") as HTMLButtonElement;
    let clicked = false;
    submit.addEventListener("click", () => (clicked = true));
    const outcome = detectAdapter({ url: "https://boards.greenhouse.io/affirm/jobs/1", document })!;
    await runAutofill(session(), outcome, { fetchDocument: async () => null });
    expect(clicked).toBe(false);
  });
});
