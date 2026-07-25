/**
 * Fixture-based end-to-end test for an employer-hosted careers application
 * form on a domain with no dedicated ATS adapter (the MongoDB Careers shape:
 * First/Last/Preferred name, Country select, City, Website, LinkedIn, and
 * separate resume/cover-letter uploads). Exercises the real content-script
 * pipeline (detect -> discover -> map -> fill -> upload) with no mocking.
 */

import { describe, expect, it } from "vitest";
import { detectAdapter } from "../ats/registry";
import { pickApplicationForm } from "../ats/base";
import { runAutofill } from "../content/autofill";
import { scan } from "../fields/runner";
import type { ApplicationSessionData } from "../types";
import { CUSTOM_EMPLOYER_FIXTURE, mountFixture } from "./fixtures";

function mongoSession(): ApplicationSessionData {
  const a = (canonical_key: string, value: string, extra: Partial<ApplicationSessionData["answers"][number]> = {}) => ({
    canonical_key, value, display_value: value, source: "profile", confidence: 0.97,
    sensitive: false, requires_review: false, verified: true, ...extra
  });
  return {
    sessionId: 99,
    atsType: null,
    officialUrl: "https://careers.mongodb.com/jobs/123/apply",
    jobTitle: "Software Engineer",
    company: "MongoDB",
    unresolvedQuestions: [],
    answers: [
      a("first_name", "Chandra"),
      a("last_name", "Pandey"),
      a("preferred_name", "Chan"),
      a("email", "cp@example.com"),
      a("phone", "+1 415 555 0100"),
      a("country", "United States"),
      a("city", "Palo Alto"),
      a("portfolio_url", "https://chandra.dev"),
      a("linkedin_url", "https://linkedin.com/in/cp")
    ]
  };
}

describe("employer-hosted careers form (no dedicated ATS adapter, e.g. MongoDB Careers)", () => {
  it("falls back to the generic adapter on an arbitrary employer domain", () => {
    const out = detectAdapter({ url: "https://careers.mongodb.com/jobs/123/apply", document: mountFixture(CUSTOM_EMPLOYER_FIXTURE) });
    expect(out?.adapter.id).toBe("generic");
  });

  it("fills First/Last/Preferred name, Email, Phone, Country, City, Website, LinkedIn; attempts resume and cover-letter upload separately; never submits", async () => {
    mountFixture(CUSTOM_EMPLOYER_FIXTURE);
    const outcome = detectAdapter({ url: "https://careers.mongodb.com/jobs/123/apply", document })!;

    const submit = document.querySelector('button[type="submit"]') as HTMLButtonElement;
    let submitClicked = false;
    submit.addEventListener("click", () => (submitClicked = true));

    const uploadedKinds: string[] = [];
    const res = await runAutofill(mongoSession(), outcome, {
      fetchDocument: async (kind) => new File([new Uint8Array([1])], `${kind}.pdf`, { type: "application/pdf" }),
      onUploadStart: (kind) => uploadedKinds.push(kind)
    });

    expect((document.getElementById("firstName") as HTMLInputElement).value).toBe("Chandra");
    expect((document.getElementById("lastName") as HTMLInputElement).value).toBe("Pandey");
    expect((document.getElementById("preferredName") as HTMLInputElement).value).toBe("Chan");
    expect((document.getElementById("email") as HTMLInputElement).value).toBe("cp@example.com");
    expect((document.getElementById("phone") as HTMLInputElement).value).toContain("415");
    expect((document.getElementById("city") as HTMLInputElement).value).toBe("Palo Alto");
    expect((document.getElementById("website") as HTMLInputElement).value).toBe("https://chandra.dev");
    expect((document.getElementById("linkedin") as HTMLInputElement).value).toBe("https://linkedin.com/in/cp");
    expect((document.getElementById("country") as HTMLSelectElement).value).toBe("US");

    // Both documents are attempted, one upload per document, never swapped.
    // (The actual DataTransfer file assignment is a real-browser API not
    // implemented by jsdom — covered by the extension's manual/e2e checks;
    // the resume-vs-cover-letter classification itself is asserted below.)
    expect(uploadedKinds.sort()).toEqual(["cover-letter", "resume"]);

    expect(res.result.fields_filled).toBeGreaterThanOrEqual(8);
    expect(submitClicked).toBe(false);
  });

  it("classifies the resume and cover-letter file inputs separately (never cross-uploads)", () => {
    mountFixture(CUSTOM_EMPLOYER_FIXTURE);
    const { fields, mappings } = scan(pickApplicationForm(document), mongoSession());
    const idFor = (uid: string) => fields.find((f) => f.uid === uid)?.id;

    const resumeMap = mappings.find((m) => m.canonicalKey === "resume_upload");
    const coverMap = mappings.find((m) => m.canonicalKey === "cover_letter_upload");
    expect(resumeMap).toBeTruthy();
    expect(coverMap).toBeTruthy();
    expect(idFor(resumeMap!.uid)).toBe("resume");
    expect(idFor(coverMap!.uid)).toBe("coverLetter");
  });
});
