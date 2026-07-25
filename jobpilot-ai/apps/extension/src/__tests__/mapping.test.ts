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
      { canonical_key: "work_authorization_us", value: "Yes", display_value: "Yes", source: "profile", confidence: 0.99, sensitive: false, requires_review: false, verified: true },
      { canonical_key: "custom_motivation", value: "Acme builds the kind of dependable backend products I enjoy working on.", display_value: "Acme builds the kind of dependable backend products I enjoy working on.", source: "openai:gpt", confidence: 0.9, sensitive: false, requires_review: true, verified: false }
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

  it("classifies the Lyft consent, work-history, authorization, and signature questions", () => {
    document.body.innerHTML = `<form>
      <label>May we contact your current employer?<select id="contact"><option>Yes</option><option>No</option></select></label>
      <label>Can you perform these essential functions of the job with reasonable accommodation?<select id="functions"><option>Yes</option><option>No</option></select></label>
      <label>Work Authorization<select id="authorization"><option>Authorized to work</option><option>Not authorized</option></select></label>
      <label>Have you been employed by Lyft, or any subsidiary, affiliate, or business unit of Lyft, in the past?<select id="previous"><option>Yes</option><option>No</option></select></label>
      <label>I certify that the facts set forth in this Application for Employment are true and complete to the best of my knowledge.<input id="signature"><small>Please enter your full name and today's date to signify your electronic signature.</small></label>
    </form>`;
    const classified = new Map(
      discoverFields(document.querySelector("form")!).map((field) => [field.id, classifyField(field).canonicalKey])
    );
    expect(classified.get("contact")).toBe("contact_current_employer");
    expect(classified.get("functions")).toBe("essential_functions_with_accommodation");
    expect(classified.get("authorization")).toBe("work_authorization_us");
    expect(classified.get("previous")).toBe("previously_employed");
    expect(classified.get("signature")).toBe("electronic_signature");
  });

  it("maps an exact standalone Name label to the saved full name", () => {
    document.body.innerHTML = `<form><label>Name*<input id="candidate_name" /></label></form>`;
    const field = discoverFields(document.querySelector("form")!)[0];
    const classification = classifyField(field);
    expect(classification.canonicalKey).toBe("full_name");
    expect(classification.confidence).toBeGreaterThanOrEqual(0.95);
  });

  it("recognizes a short company-specific why question without swallowing behavioural why questions", () => {
    document.body.innerHTML = `<form>
      <label for="company-why">Why Anthropic? *</label><textarea id="company-why"></textarea>
      <label for="departure">Why did you leave your last role?</label><textarea id="departure"></textarea>
    </form>`;
    const classified = new Map(
      discoverFields(document.querySelector("form")!).map((field) => [field.id, classifyField(field).canonicalKey])
    );
    expect(classified.get("company-why")).toBe("custom_motivation");
    expect(classified.get("departure")).not.toBe("custom_motivation");
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

    // An explicit Profile work-authorization selection is verified and filled.
    expect(pick("work_auth").canonicalKey).toBe("work_authorization_us");
    expect(pick("work_auth").safeToAutoFill).toBe(true);
    expect(pick("work_auth").requiresReview).toBe(false);

    // Sensitive gender → never auto-filled.
    expect(pick("gender").sensitive).toBe(true);
    expect(pick("gender").safeToAutoFill).toBe(false);

    // Uploads are safe to act on.
    expect(pick("resume").safeToAutoFill).toBe(true);

    // Generated written response → inserted for editing and always reviewed.
    expect(pick("why").safeToAutoFill).toBe(true);
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
