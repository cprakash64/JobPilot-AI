import { describe, expect, it } from "vitest";
import { detectAdapter } from "../ats/registry";
import { discoverFields } from "../fields/discovery";
import { GREENHOUSE_FIXTURE, mountFixture } from "./fixtures";

describe("field discovery", () => {
  it("discovers labeled fields and skips honeypot + disabled fields", () => {
    mountFixture(GREENHOUSE_FIXTURE);
    const fields = discoverFields(document.querySelector("#application_form")!);
    const byId = new Map(fields.map((f) => [f.id, f]));

    expect(byId.has("first_name")).toBe(true);
    expect(byId.get("first_name")!.label).toContain("First Name");
    expect(byId.get("first_name")!.required).toBe(true);
    expect(byId.get("email")!.autocomplete).toBe("email");

    // File inputs discovered as file controls.
    expect(byId.get("resume")!.control).toBe("file");
    // Select captures its options.
    expect(byId.get("work_auth")!.control).toBe("select");
    expect(byId.get("work_auth")!.options).toContain("Yes");

    // Honeypot (off-screen) and disabled fields are excluded.
    expect(fields.some((f) => f.name.includes("honeypot"))).toBe(false);
    expect(byId.has("disabled_field")).toBe(false);
  });

  it("captures section headings for sensitive groups", () => {
    mountFixture(GREENHOUSE_FIXTURE);
    const fields = discoverFields(document.querySelector("#application_form")!);
    const gender = fields.find((f) => f.id === "gender")!;
    expect(gender.sectionHeading).toMatch(/Voluntary Self-Identification/i);
  });

  it("finds nothing on an empty shell and finds fields once they render — the scenario the content script's bounded MutationObserver retry loop waits for", () => {
    mountFixture(`<main id="app"></main>`);
    const url = "https://careers.mongodb.com/jobs/123/apply";
    // Nothing rendered yet: no adapter match, zero fields — the content script
    // keeps polling rather than giving up immediately.
    expect(detectAdapter({ url, document })).toBeNull();
    expect(discoverFields(document.querySelector("#app")!).length).toBe(0);

    // The SPA renders the form asynchronously (e.g. after a data fetch).
    document.querySelector("#app")!.innerHTML = GREENHOUSE_FIXTURE;

    const outcome = detectAdapter({ url, document });
    expect(outcome).not.toBeNull();
    expect(discoverFields(document.querySelector("#app")!).length).toBeGreaterThan(0);
  });

  it("binds current Greenhouse React Select wrappers to each inner combobox label", () => {
    mountFixture(`
      <style>.iti__hide { display: none; }</style>
      <form id="application-form">
        <div class="application--questions">
          <div class="field-wrapper">
            <label id="linkedin-label" for="linkedin">LinkedIn Profile</label>
            <input id="linkedin" />
          </div>
          <div class="field-wrapper">
            <label id="heard-label" for="heard">How did you hear about this job?<span>*</span></label>
            <div class="select-shell">
              <div class="select__control remix-css-13cymwt-control">
                <div class="select__value-container">
                  <input id="heard" class="select__input" role="combobox"
                    aria-labelledby="heard-label" aria-required="true" aria-expanded="false" />
                </div>
              </div>
            </div>
          </div>
          <div class="field-wrapper">
            <label id="auth-label" for="auth">Are you legally authorized to work in this country?<span>*</span></label>
            <div class="select__control remix-css-13cymwt-control">
              <input id="auth" class="select__input" role="combobox"
                aria-labelledby="auth-label" aria-required="true" aria-expanded="false" />
            </div>
          </div>
          <div class="iti__hide">
            <input id="iti-search" role="combobox" />
            <ul id="iti-list" role="listbox"><li role="option">United States</li></ul>
          </div>
        </div>
      </form>`);

    const fields = discoverFields(document.querySelector("#application-form")!);
    const heard = fields.find((field) => field.id === "heard");
    const auth = fields.find((field) => field.id === "auth");

    expect(heard?.label).toContain("How did you hear about this job?");
    expect(heard?.label).not.toContain("LinkedIn Profile");
    expect(heard?.required).toBe(true);
    expect(auth?.label).toContain("legally authorized");
    expect(auth?.required).toBe(true);
    expect(fields.some((field) => field.id === "iti-search" || field.id === "iti-list")).toBe(false);
  });
});
