import { describe, expect, it } from "vitest";
import { discoverFields } from "../fields/discovery";
import { buildMappings, classifyField } from "../fields/mapping";
import type { ApplicationSessionData } from "../types";
import { GREENHOUSE_FIXTURE, mountFixture } from "./fixtures";

function session(): ApplicationSessionData {
  return {
    sessionId: 1,
    atsType: "greenhouse",
    officialUrl: "https://boards.greenhouse.io/acme/1",
    jobTitle: "Backend Engineer",
    company: "Acme",
    unresolvedQuestions: [{ canonical_key: "gender", reason: "EEO" }],
    answers: [
      { canonical_key: "first_name", value: "Chandra", display_value: "Chandra", source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified: true },
      { canonical_key: "last_name", value: "Pandey", display_value: "Pandey", source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified: true },
      { canonical_key: "email", value: "cp@example.com", display_value: "cp@example.com", source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified: true },
      { canonical_key: "phone", value: "602-555-0100", display_value: "602-555-0100", source: "profile", confidence: 0.9, sensitive: false, requires_review: false, verified: false },
      { canonical_key: "work_authorization_us", value: "Yes", display_value: "Yes", source: "profile", confidence: 0.9, sensitive: false, requires_review: true, verified: false }
    ]
  };
}

function fields() {
  mountFixture(GREENHOUSE_FIXTURE);
  return discoverFields(document.querySelector("#application_form")!);
}

describe("field classification", () => {
  it("classifies common fields deterministically", () => {
    const map = new Map(fields().map((f) => [f.id, classifyField(f)]));
    expect(map.get("first_name")!.canonicalKey).toBe("first_name");
    expect(map.get("email")!.canonicalKey).toBe("email");
    expect(map.get("email")!.source).toBe("autocomplete");
    expect(map.get("linkedin")!.canonicalKey).toBe("linkedin_url");
    expect(map.get("resume")!.canonicalKey).toBe("resume_upload");
    expect(map.get("cover")!.canonicalKey).toBe("cover_letter_upload");
    expect(map.get("why")!.canonicalKey).toBe("custom_motivation");
    expect(map.get("gender")!.sensitive).toBe(true);
  });
});

describe("mapping + confidence policy", () => {
  it("auto-fills verified facts and reviews unverified sensitive fields", () => {
    const discovered = fields();
    const mm = buildMappings(discovered, session()).mappings;
    const pick = (id: string) => mm.find((m) => m.uid === discovered.find((f) => f.id === id)!.uid)!;

    // Verified email/first name → safe to auto-fill.
    expect(pick("email").safeToAutoFill).toBe(true);
    expect(pick("email").requiresReview).toBe(false);
    expect(pick("first_name").safeToAutoFill).toBe(true);

    // Consequential + unverified (work auth) → fill but flag for review.
    expect(pick("work_auth").canonicalKey).toBe("work_authorization_us");
    expect(pick("work_auth").safeToAutoFill).toBe(true);
    expect(pick("work_auth").requiresReview).toBe(true);

    // Sensitive gender → never auto-filled.
    expect(pick("gender").sensitive).toBe(true);
    expect(pick("gender").safeToAutoFill).toBe(false);

    // Uploads are safe to act on.
    expect(pick("resume").safeToAutoFill).toBe(true);

    // Generated written response → suggested + reviewed, not silently filled.
    expect(pick("why").safeToAutoFill).toBe(false);
    expect(pick("why").requiresReview).toBe(true);

    // No linkedin answer in session → mapped but not auto-filled.
    expect(pick("linkedin").canonicalKey).toBe("linkedin_url");
    expect(pick("linkedin").safeToAutoFill).toBe(false);
  });

  it("fills a sensitive value only when the backend explicitly verified and enabled it", () => {
    const discovered = fields();
    const optedIn = session();
    optedIn.answers.push({
      canonical_key: "gender",
      value: "Prefer not to say",
      display_value: "Prefer not to say",
      source: "answer_vault",
      confidence: 1,
      sensitive: true,
      requires_review: false,
      verified: true
    });

    const genderField = discovered.find((f) => f.id === "gender")!;
    const gender = buildMappings(discovered, optedIn).mappings.find((m) => m.uid === genderField.uid)!;
    expect(gender.sensitive).toBe(true);
    expect(gender.safeToAutoFill).toBe(true);
    expect(gender.requiresReview).toBe(false);
  });
});
