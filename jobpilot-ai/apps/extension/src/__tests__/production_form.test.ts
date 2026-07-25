import { describe, expect, it } from "vitest";

import { applyFill, scan } from "../fields/runner";
import { classifyField } from "../fields/mapping";
import { discoverFields } from "../fields/discovery";
import { buildReviewModel } from "../content/review";
import { statusFromResult, type LedgerEntry } from "../fields/ledger";
import {
  closestGpaOption,
  graduationYearOptionMatches
} from "../fields/aliases";
import type { ApplicationSessionData } from "../types";

function answer(
  canonical_key: string,
  value: string,
  sensitive = false
): ApplicationSessionData["answers"][number] {
  return {
    canonical_key,
    value,
    display_value: value,
    source: sensitive ? "profile_eeo" : "profile",
    confidence: 1,
    sensitive,
    requires_review: false,
    verified: true
  };
}

function session(): ApplicationSessionData {
  return {
    sessionId: 1,
    atsType: "greenhouse",
    officialUrl: "https://boards.greenhouse.io/example/jobs/1",
    jobTitle: "Engineer",
    company: "Example",
    profileData: {
      location: "Tempe, Arizona, United States"
    },
    unresolvedQuestions: [],
    answers: [
      answer("phone", "+16028161309"),
      answer("phone_country", "+1"),
      answer("phone_country_iso2", "US"),
      answer("phone_national", "6028161309"),
      answer("city", "Tempe"),
      answer("education_degree", "Master of Science"),
      answer("education_major", "Computer Science"),
      answer("education_end_year", "2025"),
      answer("education_gpa", "3.82"),
      answer("willing_to_relocate", "Yes"),
      answer("race", "Asian", true)
    ]
  };
}

describe("production application regressions", () => {
  it("uses the exact US dialing option and fills common education/EEO fields", async () => {
    document.body.innerHTML = `
      <form>
        <div>
          <label for="dial-country">Country *</label>
          <select id="dial-country" required>
            <option value="">Select…</option>
            <option value="CV">Cape Verde (+238)</option>
            <option value="US">United States (+1)</option>
          </select>
          <label for="phone">Phone *</label>
          <input id="phone" type="tel" required>
        </div>
        <label>Location (City) *
          <select id="city" required>
            <option value="">Select…</option>
            <option value="Tempe">Tempe, AZ</option>
            <option value="Phoenix">Phoenix, AZ</option>
          </select>
        </label>
        <h2>Education</h2>
        <label>Degree *
          <select id="degree" required>
            <option value="">Select…</option>
            <option>Master's Degree</option>
            <option>Bachelor's Degree</option>
          </select>
        </label>
        <label>Discipline *
          <select id="major" required>
            <option value="">Select…</option>
            <option>Computer Science</option>
          </select>
        </label>
        <label>End date year *<input id="end-year" type="number" required></label>
        <label>What year did you graduate from your most recent degree program? *
          <select id="graduation-period" required>
            <option value="">Select…</option>
            <option>2023-2026</option>
            <option>2020-2023</option>
            <option>Before 2020</option>
          </select>
        </label>
        <label>What is your major GPA (on a 4.0 scale) for your most recent degree program? *
          <select id="gpa" required>
            <option value="">Select…</option>
            <option>3.50-3.74</option>
            <option>3.75-4.00</option>
          </select>
        </label>
        <label>This role requires working onsite. Are you currently local or willing to relocate? *
          <select id="relocation" required><option value="">Select…</option><option>Yes</option><option>No</option></select>
        </label>
        <label>Please identify your race *
          <select id="race" required><option value="">Select…</option><option>Asian</option><option>White</option></select>
        </label>
      </form>`;

    const root = document.querySelector("form")!;
    const { fields, mappings } = scan(root, session());
    const result = await applyFill(fields, mappings, session());

    expect((document.querySelector("#dial-country") as HTMLSelectElement).value).toBe("US");
    expect((document.querySelector("#dial-country") as HTMLSelectElement).value).not.toBe("CV");
    expect((document.querySelector("#phone") as HTMLInputElement).value).toBe("6028161309");
    expect((document.querySelector("#city") as HTMLSelectElement).value).toBe("Tempe");
    expect((document.querySelector("#degree") as HTMLSelectElement).value).toBe("Master's Degree");
    expect((document.querySelector("#major") as HTMLSelectElement).value).toBe("Computer Science");
    expect((document.querySelector("#end-year") as HTMLInputElement).value).toBe("2025");
    expect((document.querySelector("#graduation-period") as HTMLSelectElement).value).toBe("2023-2026");
    expect((document.querySelector("#gpa") as HTMLSelectElement).value).toBe("3.75-4.00");
    expect((document.querySelector("#relocation") as HTMLSelectElement).value).toBe("Yes");
    expect((document.querySelector("#race") as HTMLSelectElement).value).toBe("Asian");
    expect(result.errors).toEqual([]);
  });

  it("treats an employer country selection that is already present as answered", () => {
    document.body.innerHTML = `<label for="dial-country">Country *</label>
      <select id="dial-country" required>
        <option value="">Select…</option>
        <option value="CV">Cape Verde (+238)</option>
        <option value="US" selected>United States (+1)</option>
      </select>`;
    const field = discoverFields(document).find((item) => item.id === "dial-country")!;
    const terminal = statusFromResult(field, {
      uid: field.uid,
      fieldKey: "phone_country",
      question: "Country",
      status: "skipped",
      reasonCode: "USER_VALUE_PRESENT",
      confidence: 1
    });
    const entry: LedgerEntry = {
      uid: field.uid, frameId: field.frameId, label: "Country", normalizedLabel: "country",
      controlType: field.control, canonicalKey: "phone_country", required: true, sensitive: false,
      options: field.options, multiple: false, currentValuePresent: true,
      status: terminal.status, reasonCode: terminal.reasonCode, fillSource: "user",
      verified: terminal.verified, question: "Country", reusable: true, defaultScope: "global"
    };

    expect(terminal.status).toBe("user_entered");
    expect(buildReviewModel([entry], session()).items).toEqual([]);
  });

  it("rewrites terse education labels into questions that explain what is wanted", () => {
    const entry: LedgerEntry = {
      uid: "degree", frameId: "top", label: "Degree", normalizedLabel: "degree",
      controlType: "select", canonicalKey: "education_degree", required: true, sensitive: false,
      options: ["Bachelor's Degree", "Master's Degree"], multiple: false, currentValuePresent: false,
      status: "missing_information", reasonCode: "OPTION_NOT_AVAILABLE", fillSource: null,
      verified: false, question: "Degree", reusable: true, defaultScope: "global"
    };
    const [item] = buildReviewModel([entry], session()).items;
    expect(item.question).toBe("What type of degree did you earn?");
    expect(item.reasonText).toContain("closest degree level");
  });

  it("identifies transcript inputs instead of presenting generic Attach questions", () => {
    document.body.innerHTML = `<form>
      <label>Undergraduate Transcript *<input id="undergrad" type="file" aria-label="Attach" required></label>
      <label>Graduate Transcript<input id="graduate" type="file" aria-label="Attach"></label>
    </form>`;
    const mapped = new Map(
      discoverFields(document.querySelector("form")!).map((field) => [field.id, classifyField(field)])
    );
    expect(mapped.get("undergrad")?.canonicalKey).toBe("undergraduate_transcript_upload");
    expect(mapped.get("graduate")?.canonicalKey).toBe("graduate_transcript_upload");
  });

  it("maps explicit graduation ranges and controlled GPA conversions", () => {
    expect(graduationYearOptionMatches("2023–2026", "2025")).toBe(true);
    expect(graduationYearOptionMatches("2020-2023", "2025")).toBe(false);
    expect(graduationYearOptionMatches("Before 2020", "2019")).toBe(true);
    expect(
      closestGpaOption([{ label: "3.5" }, { label: "3.8" }, { label: "4.0" }], "3.82")?.label
    ).toBe("3.8");
  });
});
