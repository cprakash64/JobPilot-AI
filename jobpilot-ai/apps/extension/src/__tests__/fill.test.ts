import { describe, expect, it, vi } from "vitest";
import { discoverFields } from "../fields/discovery";
import { clearJobPilotFields, fillField, isJobPilotFilled } from "../fields/fill";

function mount(html: string) {
  document.body.innerHTML = `<form>${html}</form>`;
  return discoverFields(document.querySelector("form")!);
}
const field = (fields: ReturnType<typeof mount>, id: string) => fields.find((f) => f.id === id)!;

describe("fill engine", () => {
  it("fills a text input and dispatches input/change events (React-friendly)", () => {
    const fields = mount(`<label for="email">Email</label><input id="email" type="email" />`);
    const el = document.getElementById("email") as HTMLInputElement;
    const onInput = vi.fn();
    el.addEventListener("input", onInput);

    const outcome = fillField(field(fields, "email"), "cp@example.com");
    expect(outcome.status).toBe("filled");
    expect(el.value).toBe("cp@example.com");
    expect(onInput).toHaveBeenCalled();
    expect(isJobPilotFilled(el)).toBe(true);
  });

  it("does not overwrite a value the user already typed", () => {
    const fields = mount(`<label for="name">Name</label><input id="name" value="Existing User" />`);
    const outcome = fillField(field(fields, "name"), "JobPilot Name");
    expect(outcome.status).toBe("skipped");
    expect((document.getElementById("name") as HTMLInputElement).value).toBe("Existing User");
  });

  it("selects a matching option in a dropdown", () => {
    const fields = mount(
      `<label for="c">Country</label><select id="c"><option value="">--</option><option value="us">United States</option></select>`
    );
    const outcome = fillField(field(fields, "c"), "United States");
    expect(outcome.status).toBe("filled");
    expect((document.getElementById("c") as HTMLSelectElement).value).toBe("us");
  });

  it("selects a radio option by label", () => {
    const fields = mount(
      `<input id="y" type="radio" name="auth" value="yes" /><label for="y">Yes</label>
       <input id="n" type="radio" name="auth" value="no" /><label for="n">No</label>`
    );
    const outcome = fillField(field(fields, "y"), "Yes");
    expect(outcome.status).toBe("filled");
    expect((document.getElementById("y") as HTMLInputElement).checked).toBe(true);
  });

  it("toggles a checkbox from a boolean-ish value", () => {
    const fields = mount(`<input id="agree" type="checkbox" /><label for="agree">I agree</label>`);
    fillField(field(fields, "agree"), "yes");
    expect((document.getElementById("agree") as HTMLInputElement).checked).toBe(true);
  });

  it("clears only JobPilot-filled values and restores the original", () => {
    const fields = mount(
      `<label for="a">A</label><input id="a" />
       <label for="b">B</label><input id="b" value="user-typed" />`
    );
    fillField(field(fields, "a"), "jobpilot");
    // b keeps the user value (skip), so it must not be cleared.
    fillField(field(fields, "b"), "jobpilot");

    const cleared = clearJobPilotFields(document);
    expect(cleared).toBe(1); // only the field JobPilot actually filled
    expect((document.getElementById("a") as HTMLInputElement).value).toBe("");
    expect((document.getElementById("b") as HTMLInputElement).value).toBe("user-typed");
    expect(document.querySelectorAll("[data-jobpilot-filled]").length).toBe(0);
  });

  it("reports review_required when a select has no matching option", () => {
    const fields = mount(`<label for="c">Country</label><select id="c"><option value="">--</option></select>`);
    const outcome = fillField(field(fields, "c"), "Atlantis");
    expect(outcome.status).toBe("review_required");
  });
});
