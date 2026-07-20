import { describe, expect, it } from "vitest";
import { detectAdapter } from "../ats/registry";
import { discoverUploadInputs, pickApplicationForm } from "../ats/base";
import { runAutofill } from "../content/autofill";
import { discoverFields } from "../fields/discovery";
import { fillField } from "../fields/fill";
import type { ApplicationSessionData } from "../types";
import { AFFIRM_GREENHOUSE_FIXTURE, mountFixture } from "./fixtures";
import { attachDropdownComponent } from "./dropdownComponents";
import { configureDropdownTiming } from "../fields/dropdown/dom";

configureDropdownTiming({ openPointerMs: 120, openKeyboardMs: 120, openEnterMs: 50, listboxMs: 100, optionsMs: 150, verifyMs: 150, pollStepMs: 5 });

function session(): ApplicationSessionData {
  const a = (canonical_key: string, value: string, extra: Partial<ApplicationSessionData["answers"][number]> = {}) => ({
    canonical_key, value, display_value: value, source: "profile", confidence: 0.97,
    sensitive: false, requires_review: false, verified: true, ...extra
  });
  return {
    sessionId: 42,
    atsType: "greenhouse",
    officialUrl: "https://job-boards.greenhouse.io/affirm/jobs/1",
    jobTitle: "Software Engineer",
    company: "Affirm",
    unresolvedQuestions: [],
    answers: [
      a("first_name", "Chandra"),
      a("last_name", "Pandey"),
      a("email", "cp@example.com"),
      a("phone", "+1 415 555 0100"),
      a("country", "United States"),
      a("linkedin_url", "https://linkedin.com/in/cp"),
      a("work_authorization_us", "Yes")
    ]
  };
}

describe("Affirm (new Greenhouse job-boards) form", () => {
  it("detects Greenhouse on the job-boards host + React markup", () => {
    const out = detectAdapter({ url: "https://job-boards.greenhouse.io/affirm/jobs/1", document: mountFixture(AFFIRM_GREENHOUSE_FIXTURE) });
    expect(out?.adapter.id).toBe("greenhouse");
    expect(out?.result.confidence).toBeGreaterThanOrEqual(0.5);
  });

  it("discovers first/last/email/phone via associated labels + namespaced names", () => {
    mountFixture(AFFIRM_GREENHOUSE_FIXTURE);
    const fields = discoverFields(pickApplicationForm(document));
    const byId = (id: string) => fields.find((f) => f.id === id);
    expect(byId("first_name")?.label).toMatch(/first name/i);
    expect(byId("email")?.inputType).toBe("email");
    expect(byId("phone")?.control).toBe("text");
    // The custom country combobox is discovered as a text input.
    expect(byId("country")).toBeTruthy();
  });

  it("discovers hidden file inputs behind Attach and classifies resume vs cover letter", () => {
    mountFixture(AFFIRM_GREENHOUSE_FIXTURE);
    const uploads = discoverUploadInputs(pickApplicationForm(document));
    const kinds = uploads.map((u) => ({ id: u.input.id, kind: u.kind }));
    expect(kinds).toContainEqual({ id: "resume-input", kind: "resume" });
    expect(kinds).toContainEqual({ id: "cover-input", kind: "cover-letter" });
    // The cover letter is NEVER classified as a resume.
    expect(uploads.find((u) => u.input.id === "cover-input")?.kind).not.toBe("resume");
  });

  it("leaves a custom country combobox as a review item when no option ever renders (bounded wait)", async () => {
    mountFixture(AFFIRM_GREENHOUSE_FIXTURE);
    const fields = discoverFields(pickApplicationForm(document));
    const country = fields.find((f) => f.id === "country")!;
    const outcome = await fillField(country, "United States");
    // No <option role> rendered within the bounded wait → do not fake success.
    expect(outcome.status).toBe("review_required");
    expect(country.element?.getAttribute("data-jobpilot-status")).toBe("review");
  });

  it("selects a real combobox option and VERIFIES the component committed it", async () => {
    mountFixture(AFFIRM_GREENHOUSE_FIXTURE);
    // Attach real component behaviour: the menu only opens on the event
    // sequence a component listens for, and the selection only commits when the
    // option is genuinely activated. A click alone must never count as success.
    const input = document.getElementById("country") as HTMLInputElement;
    attachDropdownComponent({
      control: input,
      menu: document.getElementById("country-listbox")!,
      options: ["Canada", "United States"],
      valueInput: input
    });
    const fields = discoverFields(pickApplicationForm(document));
    const country = fields.find((f) => f.id === "country")!;
    const outcome = await fillField(country, "United States");
    expect(outcome.status).toBe("filled");
    expect(input.value).toBe("United States");
  });

  it("never reports filled when the combobox list is inert (click is not success)", async () => {
    mountFixture(AFFIRM_GREENHOUSE_FIXTURE);
    const listbox = document.getElementById("country-listbox")!;
    listbox.innerHTML = `<li role="option">Canada</li><li role="option">United States</li>`;
    const fields = discoverFields(pickApplicationForm(document));
    const country = fields.find((f) => f.id === "country")!;
    const outcome = await fillField(country, "United States");
    expect(outcome.status).toBe("review_required");
    expect(outcome.dropdown?.reasonCode).toBe("DROPDOWN_VERIFICATION_FAILED");
  });

  it("runs autofill automatically: fills verified fields, uploads docs, never clicks Submit", async () => {
    mountFixture(AFFIRM_GREENHOUSE_FIXTURE);
    const outcome = detectAdapter({ url: "https://job-boards.greenhouse.io/affirm/jobs/1", document })!;

    const submit = document.getElementById("submit_app") as HTMLButtonElement;
    let submitClicked = false;
    submit.addEventListener("click", () => (submitClicked = true));

    const pdf = new File([new Uint8Array([1, 2, 3])], "resume.pdf", { type: "application/pdf" });
    const res = await runAutofill(session(), outcome, {
      fetchDocument: async (kind) => new File([new Uint8Array([1])], `${kind}.pdf`, { type: "application/pdf" })
    });
    void pdf;

    expect((document.getElementById("first_name") as HTMLInputElement).value).toBe("Chandra");
    expect((document.getElementById("last_name") as HTMLInputElement).value).toBe("Pandey");
    expect((document.getElementById("email") as HTMLInputElement).value).toBe("cp@example.com");
    expect((document.getElementById("phone") as HTMLInputElement).value).toContain("415");

    // Detailed result model is produced.
    expect(res.result.fields_filled).toBeGreaterThanOrEqual(4);
    expect(res.fieldResults.some((r) => r.fieldKey === "first_name" && r.status === "filled")).toBe(true);
    // The work-authorization native select is filled from a verified answer.
    expect((document.getElementById("q_auth") as HTMLSelectElement).value).toBe("Yes");
    // Submit was never touched.
    expect(submitClicked).toBe(false);
  });
});
