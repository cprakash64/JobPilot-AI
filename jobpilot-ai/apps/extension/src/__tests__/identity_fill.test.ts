/**
 * Sections A/B/D — structured name and phone autofill, reproduced against the
 * Samsara/Greenhouse page shape.
 *
 * The failures being pinned:
 *   - "Chandra Prakash Pandey" filled Last Name with "PRAKASH PANDEY".
 *   - The phone number was typed before the country selector was set, so the
 *     site's mask rejected it — and "+1" was written into a field that already
 *     had a "+1" country selected.
 */

import { describe, expect, it } from "vitest";
import {
  binaryAnswerMatches,
  companyCareersSourceMatches,
  dialCodeMatches,
  locationOptionMatches,
  singletonPrivacyAcknowledgementMatches,
  singletonRequiredAffirmationMatches
} from "../fields/aliases";
import { attachDropdownComponent } from "./dropdownComponents";
import { discoverFields } from "../fields/discovery";
import { fillField } from "../fields/fill";
import { classifyField } from "../fields/mapping";
import { applyFill, scan } from "../fields/runner";
import type { ApplicationSessionData } from "../types";

function answer(key: string, value: string) {
  return {
    canonical_key: key,
    value,
    display_value: value,
    source: "profile",
    confidence: 0.99,
    sensitive: false,
    requires_review: false,
    verified: true
  };
}

/** The real profile after the structured-name confirmation. */
function session(extra: ReturnType<typeof answer>[] = []): ApplicationSessionData {
  return {
    sessionId: 1,
    atsType: "greenhouse",
    officialUrl: "https://job-boards.greenhouse.io/samsara/jobs/1",
    jobTitle: "Software Engineer",
    company: "Samsara",
    unresolvedQuestions: [],
    answers: [
      answer("first_name", "Chandra"),
      answer("middle_name", "Prakash"),
      answer("last_name", "Pandey"),
      answer("full_name", "Chandra Prakash Pandey"),
      answer("preferred_first_name", "Chandra"),
      answer("preferred_last_name", "Pandey"),
      answer("email", "cprakash.work@example.com"),
      answer("phone", "+16028161309"),
      answer("phone_country", "+1"),
      answer("phone_national", "6028161309"),
      answer("postal_code", "85281"),
      ...extra
    ]
  } as ApplicationSessionData;
}

function mount(html: string) {
  document.body.innerHTML = `<div id="app">${html}</div>`;
  return document.querySelector("#app")!;
}

const val = (id: string) => (document.getElementById(id) as HTMLInputElement).value;

async function fillAll(root: ParentNode, data: ApplicationSessionData) {
  const { fields, mappings } = scan(root, data);
  return { summary: await applyFill(fields, mappings, data), mappings };
}

// --------------------------------------------------------------------------- //
// D — name-field classification
// --------------------------------------------------------------------------- //
describe("name field classification", () => {
  const classifyLabel = (label: string, id: string) => {
    document.body.innerHTML = `<form><label for="${id}">${label}</label><input id="${id}" /></form>`;
    const fields = discoverFields(document.querySelector("form")!);
    return classifyField(fields.find((f) => f.id === id)!).canonicalKey;
  };

  it("recognizes legal first, middle, and last name separately", () => {
    expect(classifyLabel("First Name", "a")).toBe("first_name");
    expect(classifyLabel("Middle Name", "b")).toBe("middle_name");
    expect(classifyLabel("Middle Initial", "c")).toBe("middle_name");
    expect(classifyLabel("Last Name", "d")).toBe("last_name");
  });

  it("distinguishes a full/legal name field from a last-name field", () => {
    expect(classifyLabel("Full Name", "e")).toBe("full_name");
    expect(classifyLabel("Legal Name", "f")).toBe("full_name");
    expect(classifyLabel("Family Name", "g")).toBe("last_name");
  });

  it("recognizes preferred first and last name distinctly", () => {
    expect(classifyLabel("Preferred First Name", "h")).toBe("preferred_first_name");
    expect(classifyLabel("Preferred Last Name", "i")).toBe("preferred_last_name");
  });
});

describe("structured name autofill (Samsara shape)", () => {
  it("never writes the middle name into the last-name field", async () => {
    const root = mount(`
      <label for="first_name">First Name *</label><input id="first_name" required />
      <label for="last_name">Last Name *</label><input id="last_name" required />
    `);
    await fillAll(root, session());

    expect(val("first_name")).toBe("Chandra");
    // The regression, pinned: NOT "Prakash Pandey".
    expect(val("last_name")).toBe("Pandey");
  });

  it("gives a true full-name field the complete name", async () => {
    const root = mount(`<label for="full_name">Full Name *</label><input id="full_name" required />`);
    await fillAll(root, session());
    expect(val("full_name")).toBe("Chandra Prakash Pandey");
  });

  it("fills preferred first/last names from the confirmed legal name", async () => {
    const root = mount(`
      <label for="pf">Preferred First Name</label><input id="pf" />
      <label for="pl">Preferred Last Name</label><input id="pl" />
    `);
    await fillAll(root, session());
    expect(val("pf")).toBe("Chandra");
    expect(val("pl")).toBe("Pandey");
  });

  it("fills a middle-name field when the form asks for one", async () => {
    const root = mount(`<label for="mn">Middle Name</label><input id="mn" />`);
    await fillAll(root, session());
    expect(val("mn")).toBe("Prakash");
  });

  it("fills the other basic identity fields the live run left empty", async () => {
    const root = mount(`
      <label for="email">Email *</label><input id="email" type="email" required />
      <label for="zip">Zip Code *</label><input id="zip" required />
    `);
    await fillAll(root, session());
    expect(val("email")).toBe("cprakash.work@example.com");
    expect(val("zip")).toBe("85281");
  });
});

// --------------------------------------------------------------------------- //
// B — structured phone
// --------------------------------------------------------------------------- //
describe("structured phone autofill", () => {
  it("writes E.164 when the form has a single phone input", async () => {
    const root = mount(`<label for="phone">Phone *</label><input id="phone" type="tel" required />`);
    await fillAll(root, session());
    expect(val("phone")).toBe("+16028161309");
  });

  it("writes only the national number when the country is a separate control", async () => {
    const root = mount(`
      <label for="pc">Country code *</label>
      <select id="pc" required><option value="">--</option><option value="+1">United States (+1)</option></select>
      <label for="phone">Phone *</label><input id="phone" type="tel" required />
    `);
    await fillAll(root, session());

    // I.7 — "+1" appears exactly once, in the country control.
    expect((document.getElementById("pc") as HTMLSelectElement).value).toBe("+1");
    expect(val("phone")).toBe("6028161309");
    expect(val("phone")).not.toContain("+1");
  });

  it("selects the phone country BEFORE typing the number", async () => {
    const order: string[] = [];
    const root = mount(`
      <label for="phone">Phone *</label><input id="phone" type="tel" required />
      <label for="pc">Country code *</label>
      <select id="pc" required><option value="">--</option><option value="+1">United States (+1)</option></select>
    `);
    // Note the DOM order is phone-then-country; the cascade must still run
    // country first, because selecting it re-masks the phone input.
    document.getElementById("pc")!.addEventListener("change", () => order.push("country"));
    document.getElementById("phone")!.addEventListener("input", () => order.push("phone"));

    await fillAll(root, session());
    expect(order[0]).toBe("country");
    expect(order).toContain("phone");
  });

  it("accepts a site that reformats the number, but not one that mangles it", async () => {
    const root = mount(`<label for="phone">Phone *</label><input id="phone" type="tel" required />`);
    const el = document.getElementById("phone") as HTMLInputElement;
    // A typical input mask: rewrites on blur, same digits.
    el.addEventListener("blur", () => {
      const d = el.value.replace(/\D/g, "").slice(-10);
      if (d.length === 10) el.value = `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
    });
    const { summary } = await fillAll(root, session());
    expect(val("phone")).toBe("(602) 816-1309");
    expect(summary.errors).toEqual([]);
    expect(summary.filled).toBeGreaterThanOrEqual(1);
  });

  it("reports review_required when the site silently rejects the number", async () => {
    const root = mount(`<label for="phone">Phone *</label><input id="phone" type="tel" required />`);
    const el = document.getElementById("phone") as HTMLInputElement;
    // The live Greenhouse failure: the field clears itself because the country
    // was wrong. That must never be reported as filled.
    el.addEventListener("blur", () => { el.value = ""; });

    const { fields } = scan(root, session());
    const phoneField = fields.find((f) => f.id === "phone")!;
    const outcome = await fillField(phoneField, "+16028161309", {
      verify: (final) => final.replace(/\D/g, "").endsWith("6028161309")
    });
    expect(outcome.status).toBe("review_required");
  });

  it("treats aria-invalid=true as a failure, not a fill", async () => {
    const root = mount(`<label for="phone">Phone *</label><input id="phone" type="tel" required />`);
    const el = document.getElementById("phone") as HTMLInputElement;
    el.addEventListener("blur", () => el.setAttribute("aria-invalid", "true"));

    const { fields } = scan(root, session());
    const outcome = await fillField(fields.find((f) => f.id === "phone")!, "+16028161309");
    expect(outcome.status).toBe("review_required");
  });

  it("matches a dial code across the many ways selectors render it", () => {
    for (const label of ["United States (+1)", "US +1", "+1", "United States of America +1"]) {
      expect(dialCodeMatches(label, "+1")).toBe(true);
    }
  });

  it("keeps dial-code matching narrow — it never fires on ordinary options", () => {
    // Wrong country.
    expect(dialCodeMatches("United Kingdom (+44)", "+1")).toBe(false);
    // "+1" must not match "+12" or "+91".
    expect(dialCodeMatches("Kazakhstan (+7)", "+1")).toBe(false);
    expect(dialCodeMatches("India (+91)", "+1")).toBe(false);
    // An option with no dial code is never a match — the field becomes a
    // question instead of a wrong guess.
    expect(dialCodeMatches("United States", "+1")).toBe(false);
    // Only a bare dial code engages the rule at all.
    expect(dialCodeMatches("United States (+1)", "United States")).toBe(false);
    expect(dialCodeMatches("Yes", "Yes")).toBe(false);
  });

  it("classifies a phone country selector as phone_country, not phone", () => {
    document.body.innerHTML = `<form>
      <label for="cc">Country code</label><select id="cc"><option>United States (+1)</option></select>
    </form>`;
    const fields = discoverFields(document.querySelector("form")!);
    expect(classifyField(fields.find((f) => f.id === "cc")!).canonicalKey).toBe("phone_country");
  });

  it("maps canonical binary facts to verbose employer options without fuzzy guessing", () => {
    expect(binaryAnswerMatches(
      "Yes, I am currently legally authorized to work in the United States.",
      "authorized_us"
    )).toBe(true);
    expect(binaryAnswerMatches(
      "No, I do not and will not require sponsorship.",
      "No"
    )).toBe(true);
    expect(binaryAnswerMatches("No, I am not authorized to work.", "authorized_us")).toBe(false);
    expect(binaryAnswerMatches("Other", "Yes")).toBe(false);
    expect(binaryAnswerMatches("Yes", "other")).toBe(false);
  });

  it("matches only the employer careers source for the referral default", () => {
    const sentinel = "__jobpilot_company_careers_page__";
    expect(companyCareersSourceMatches("Company Website (Airbnb Careers)", sentinel)).toBe(true);
    expect(companyCareersSourceMatches("Company website", sentinel)).toBe(true);
    expect(companyCareersSourceMatches("Employee referral", sentinel)).toBe(false);
    expect(companyCareersSourceMatches("Third-party website or search engine", sentinel)).toBe(false);
  });

  it("matches a privacy acknowledgement only when it is the single substantive choice", () => {
    const sentinel = "__jobpilot_privacy_acknowledgement__";
    const acknowledgement = "I acknowledge that I have read the Candidate Privacy Policy.";
    expect(singletonPrivacyAcknowledgementMatches(acknowledgement, sentinel, [acknowledgement])).toBe(true);
    expect(singletonPrivacyAcknowledgementMatches("Yes", sentinel, ["Yes"])).toBe(true);
    expect(singletonPrivacyAcknowledgementMatches(acknowledgement, sentinel, [acknowledgement, "I decline"])).toBe(false);
  });

  it("matches a mandatory affirmative only when it is the single substantive choice", () => {
    const sentinel = "__jobpilot_required_singleton_affirmation__";
    expect(singletonRequiredAffirmationMatches("I agree", sentinel, ["I agree"])).toBe(true);
    expect(singletonRequiredAffirmationMatches("Yes", sentinel, ["Yes"])).toBe(true);
    expect(singletonRequiredAffirmationMatches("Yes", sentinel, ["Yes", "No"])).toBe(false);
  });

  it("matches a city autocomplete result only with profile disambiguators", () => {
    expect(locationOptionMatches("Phoenix, Arizona, United States", "Phoenix, AZ, United States")).toBe(true);
    expect(locationOptionMatches("Tempe, AZ", "Tempe, Arizona, United States")).toBe(true);
    expect(locationOptionMatches("Phoenix, Oregon, United States", "Phoenix, AZ, United States")).toBe(false);
    expect(locationOptionMatches("Phoenix, Arizona, United States", "Phoenix")).toBe(false);
  });

  it("fills the exact Airbnb city, authorization, sponsorship, and singleton controls from Profile data", async () => {
    document.body.innerHTML = `<form id="application">
      <div class="phone-row">
        <div class="field" data-required="true">
          <span id="phoneCountry-label">Country *</span>
          <div id="phoneCountry" class="select__control" role="combobox" aria-labelledby="phoneCountry-label"
               aria-expanded="false" aria-controls="phoneCountry-menu">
            <span class="select__placeholder">Select...</span><span class="select__value"></span>
            <input class="select__input" type="text" />
          </div>
        </div>
        <div class="field" data-required="true"><label for="phoneInput">Phone *</label><input id="phoneInput" name="phone" type="tel" /></div>
      </div>
      ${[
        ["location", "Location (City) *"],
        ["authorization", "Are you legally authorized to work in the country where the job is located? *"],
        ["sponsorship", "Will you now or in the future require company sponsorship to retain or extend your work authorization in the country where the job is located? *"],
        ["ai", "Candidate AI Usage Attestation: *"]
      ].map(([id, label]) => `<div class="field" data-required="true">
        <span id="${id}-label">${label}</span>
        <div id="${id}" class="select__control" role="combobox" aria-labelledby="${id}-label"
             aria-expanded="false" aria-controls="${id}-menu">
          <span class="select__placeholder">Select...</span><span class="select__value"></span>
          <input class="select__input" type="text" />
        </div>
      </div>`).join("")}
    </form>
    ${["phoneCountry", "location", "authorization", "sponsorship", "ai"].map((id) => `<div id="${id}-menu" role="listbox"></div>`).join("")}`;

    const options: Record<string, string[]> = {
      phoneCountry: ["United States (+1)", "India (+91)"],
      location: ["Tempe, AZ", "Tempe, Oregon"],
      authorization: [
        "Yes, I am legally authorized to work in the country where the job is located.",
        "No, I am not legally authorized to work in the country where the job is located."
      ],
      sponsorship: [
        "Yes, I will require immigration sponsorship now to legally work in the country where the job is located.",
        "Yes, I will require immigration sponsorship in the future to legally work in the country where the job is located.",
        "No, I do not and will not require immigration sponsorship to legally work in the country where the job is located."
      ],
      ai: ["I agree"]
    };
    for (const [id, values] of Object.entries(options)) {
      const control = document.getElementById(id)!;
      attachDropdownComponent({
        control,
        menu: document.getElementById(`${id}-menu`)!,
        options: values,
        display: control.querySelector<HTMLElement>(".select__value")!,
        searchInput: control.querySelector<HTMLInputElement>(".select__input")!,
        requireSearch: id === "location"
      });
    }

    const data = session([
      answer("city", "Tempe"),
      answer("work_authorization_us", "authorized_us"),
      answer("sponsorship_required_future", "No")
    ]);
    data.profileData = { location: "Tempe, Arizona, United States" };
    const { fields, mappings } = scan(document, data);
    const result = await applyFill(fields, mappings, data);

    const selected = (id: string) => document.getElementById(id)?.querySelector(".select__singleValue")?.textContent;
    const locationField = fields.find((field) => field.id === "location")!;
    expect(result.outcomes.get(locationField.uid)).toMatchObject({ status: "filled" });
    expect(selected("phoneCountry")).toBe("United States (+1)");
    expect((document.getElementById("phoneInput") as HTMLInputElement).value).toContain("602");
    expect(selected("location")).toBe("Tempe, AZ");
    expect(selected("authorization")).toMatch(/^Yes/);
    expect(selected("sponsorship")).toMatch(/^No/);
    expect(selected("ai")).toBe("I agree");
    expect(result.filled).toBe(6);
  });
});
