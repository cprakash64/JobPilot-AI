/**
 * Fixture-based end-to-end test for the actual Temporal application page, which
 * runs on Ashby (jobs.ashbyhq.com/temporal/…/application — verified against the
 * job's application_url, not appearance). Exercises the real content-script
 * pipeline (discover → map → fill → upload classification) with no mocking of
 * the field-filling logic.
 */

import { describe, expect, it } from "vitest";
import { detectAdapter } from "../ats/registry";
import { clearJobPilotFields } from "../fields/fill";
import { runFill, scan } from "../fields/runner";
import type { ApplicationSessionData } from "../types";
import { TEMPORAL_ASHBY_FIXTURE, mountFixture } from "./fixtures";

function temporalSession(): ApplicationSessionData {
  const a = (canonical_key: string, value: string, confidence = 0.97) => ({
    canonical_key, value, display_value: value, source: "profile", confidence,
    sensitive: false, requires_review: false, verified: true
  });
  return {
    sessionId: 42,
    atsType: "ashby",
    officialUrl: "https://jobs.ashbyhq.com/temporal/2a3526f9/application",
    jobTitle: "Staff Software Engineer, Nexus SDK",
    company: "Temporal",
    unresolvedQuestions: [{ canonical_key: "gender" }, { canonical_key: "legal_attestation" }],
    answers: [
      a("full_name", "Chandra Prakash Pandey"),
      a("first_name", "Chandra"),
      a("last_name", "Pandey"),
      a("email", "chandra@example.com"),
      a("phone", "602-555-0142"),
      a("linkedin_url", "https://linkedin.com/in/chandra"),
      a("portfolio_url", "https://chandra.dev")
    ]
  };
}

describe("Temporal (Ashby) application page", () => {
  it("is detected as Ashby", () => {
    const out = detectAdapter({
      url: "https://jobs.ashbyhq.com/temporal/2a3526f9/application",
      document: mountFixture(TEMPORAL_ASHBY_FIXTURE)
    });
    expect(out?.adapter.id).toBe("ashby");
  });

  it("fills contact + profile fields, classifies uploads, and never touches sensitive/legal fields", () => {
    mountFixture(TEMPORAL_ASHBY_FIXTURE);
    const summary = runFill(document.querySelector("form")!, temporalSession());

    // Contact + profile fields are filled with verified values.
    expect((document.getElementById("_systemfield_name") as HTMLInputElement).value).toBe("Chandra Prakash Pandey");
    expect((document.getElementById("_systemfield_email") as HTMLInputElement).value).toBe("chandra@example.com");
    expect((document.getElementById("_systemfield_phone") as HTMLInputElement).value).toBe("602-555-0142");
    expect((document.getElementById("q_linkedin") as HTMLInputElement).value).toBe("https://linkedin.com/in/chandra");
    expect((document.getElementById("q_website") as HTMLInputElement).value).toBe("https://chandra.dev");

    // Resume and cover letter are separate upload targets, correctly distinguished.
    const kinds = summary.uploadTargets.map((u) => u.kind).sort();
    expect(kinds).toEqual(["cover-letter", "resume"]);

    // Sensitive demographic + legal attestation are NEVER auto-filled.
    expect((document.getElementById("q_gender") as HTMLSelectElement).value).toBe("");
    expect((document.getElementById("q_attest") as HTMLInputElement).checked).toBe(false);
    expect(summary.reviewRequired).toBeGreaterThanOrEqual(1);

    // The honeypot is never filled.
    const honeypot = document.querySelector('input[name="_hp_email"]') as HTMLInputElement;
    expect(honeypot.value).toBe("");

    expect(summary.errors).toEqual([]);
  });

  it("classifies the resume and cover-letter file inputs separately (never cross-uploads)", () => {
    // The actual DataTransfer upload is a browser API (covered by the Playwright
    // suite); here we assert the classification that guarantees the tailored
    // resume goes to the resume input and the cover letter to the cover input.
    mountFixture(TEMPORAL_ASHBY_FIXTURE);
    const { fields, mappings } = scan(document.querySelector("form")!, temporalSession());
    const idFor = (uid: string) => fields.find((f) => f.uid === uid)?.id;

    const resumeMap = mappings.find((m) => m.canonicalKey === "resume_upload");
    const coverMap = mappings.find((m) => m.canonicalKey === "cover_letter_upload");
    expect(resumeMap).toBeTruthy();
    expect(coverMap).toBeTruthy();
    expect(idFor(resumeMap!.uid)).toBe("_systemfield_resume");
    expect(idFor(coverMap!.uid)).toBe("cover_letter");
  });

  it("leaves the page pristine after clearing", () => {
    mountFixture(TEMPORAL_ASHBY_FIXTURE);
    runFill(document.querySelector("form")!, temporalSession());
    clearJobPilotFields(document);
    expect((document.getElementById("_systemfield_name") as HTMLInputElement).value).toBe("");
    expect(document.querySelectorAll("[data-jobpilot-filled]").length).toBe(0);
  });
});
