import { describe, expect, it, vi } from "vitest";
import { discoverFields } from "../fields/discovery";
import { clearJobPilotFields, fillField, isJobPilotFilled } from "../fields/fill";
import { configureDropdownTiming } from "../fields/dropdown/dom";
import { attachDropdownComponent, mountPortalReactSelect, type ComponentOptions } from "./dropdownComponents";

configureDropdownTiming({ openPointerMs: 150, openKeyboardMs: 150, openEnterMs: 60, listboxMs: 120, optionsMs: 200, verifyMs: 200, pollStepMs: 5 });

function mount(html: string) {
  document.body.innerHTML = `<form>${html}</form>`;
  return discoverFields(document.querySelector("form")!);
}
const field = (fields: ReturnType<typeof mount>, id: string) => fields.find((f) => f.id === id)!;

describe("fill engine", () => {
  it("fills a text input and dispatches input/change events (React-friendly)", async () => {
    const fields = mount(`<label for="email">Email</label><input id="email" type="email" />`);
    const el = document.getElementById("email") as HTMLInputElement;
    const onInput = vi.fn();
    el.addEventListener("input", onInput);

    const outcome = await fillField(field(fields, "email"), "cp@example.com");
    expect(outcome.status).toBe("filled");
    expect(el.value).toBe("cp@example.com");
    expect(onInput).toHaveBeenCalled();
    expect(isJobPilotFilled(el)).toBe(true);
  });

  it("does not overwrite a value the user already typed", async () => {
    const fields = mount(`<label for="name">Name</label><input id="name" value="Existing User" />`);
    const outcome = await fillField(field(fields, "name"), "JobPilot Name");
    expect(outcome.status).toBe("skipped");
    expect((document.getElementById("name") as HTMLInputElement).value).toBe("Existing User");
  });

  it("selects a matching option in a dropdown", async () => {
    const fields = mount(
      `<label for="c">Country</label><select id="c"><option value="">--</option><option value="us">United States</option></select>`
    );
    const outcome = await fillField(field(fields, "c"), "United States");
    expect(outcome.status).toBe("filled");
    expect((document.getElementById("c") as HTMLSelectElement).value).toBe("us");
  });

  it("selects a native option via a controlled alias (state abbreviation, country long-form)", async () => {
    const fields = mount(
      `<label for="s">State</label><select id="s"><option value="">--</option><option value="AZ">AZ</option></select>`
    );
    const outcome = await fillField(field(fields, "s"), "Arizona");
    expect(outcome.status).toBe("filled");
    expect((document.getElementById("s") as HTMLSelectElement).value).toBe("AZ");
  });

  it("never falls back to the first/placeholder option when the expected choice is unavailable", async () => {
    const fields = mount(
      `<label for="c">Country</label><select id="c"><option value="">Select…</option><option value="ca">Canada</option></select>`
    );
    const outcome = await fillField(field(fields, "c"), "United States");
    expect(outcome.status).toBe("review_required");
    // The placeholder must not have been silently selected.
    expect((document.getElementById("c") as HTMLSelectElement).value).toBe("");
  });

  it("selects a radio option by label", async () => {
    const fields = mount(
      `<input id="y" type="radio" name="auth" value="yes" /><label for="y">Yes</label>
       <input id="n" type="radio" name="auth" value="no" /><label for="n">No</label>`
    );
    const outcome = await fillField(field(fields, "y"), "Yes");
    expect(outcome.status).toBe("filled");
    expect((document.getElementById("y") as HTMLInputElement).checked).toBe(true);
  });

  it("toggles a checkbox from a boolean-ish value", async () => {
    const fields = mount(`<input id="agree" type="checkbox" /><label for="agree">I agree</label>`);
    await fillField(field(fields, "agree"), "yes");
    expect((document.getElementById("agree") as HTMLInputElement).checked).toBe(true);
  });

  it("clears only JobPilot-filled values and restores the original", async () => {
    const fields = mount(
      `<label for="a">A</label><input id="a" />
       <label for="b">B</label><input id="b" value="user-typed" />`
    );
    await fillField(field(fields, "a"), "jobpilot");
    // b keeps the user value (skip), so it must not be cleared.
    await fillField(field(fields, "b"), "jobpilot");

    const cleared = clearJobPilotFields(document);
    expect(cleared).toBe(1); // only the field JobPilot actually filled
    expect((document.getElementById("a") as HTMLInputElement).value).toBe("");
    expect((document.getElementById("b") as HTMLInputElement).value).toBe("user-typed");
    expect(document.querySelectorAll("[data-jobpilot-filled]").length).toBe(0);
  });

  it("reports review_required when a select has no matching option", async () => {
    const fields = mount(`<label for="c">Country</label><select id="c"><option value="">--</option></select>`);
    const outcome = await fillField(field(fields, "c"), "Atlantis");
    expect(outcome.status).toBe("review_required");
  });
});

describe("custom dropdown / combobox filling", () => {
  // These fixtures attach REAL component behaviour (see dropdownComponents.ts):
  // the menu only opens in response to the event sequence a real component
  // listens for, and a selection only commits when the option is genuinely
  // activated. A click on an inert element must therefore NEVER report success.
  function mountCombobox(options: string[], extra: Partial<ComponentOptions> = {}) {
    document.body.innerHTML = `
      <form>
        <label id="country-label" for="country">Country</label>
        <div id="country" class="select__control" role="combobox" aria-labelledby="country-label"
             aria-expanded="false" aria-controls="country-listbox">
          <span class="select__placeholder">Select...</span>
          <span class="select__value"></span>
          <input class="select__input" type="text" />
        </div>
        <ul id="country-listbox" role="listbox"></ul>
      </form>`;
    const control = document.getElementById("country")!;
    attachDropdownComponent({
      control,
      menu: document.getElementById("country-listbox")!,
      options,
      display: control.querySelector<HTMLElement>(".select__value")!,
      searchInput: control.querySelector<HTMLInputElement>(".select__input")!,
      ...extra
    });
    return discoverFields(document.querySelector("form")!);
  }

  it("opens a real component and selects the requested option (never the first)", async () => {
    const fields = mountCombobox(["Canada", "United States", "India"]);
    const outcome = await fillField(field(fields, "country"), "United States");
    expect(outcome.status).toBe("filled");
    expect(document.querySelector("#country .select__value")!.textContent).toBe("United States");
  });

  it("waits for options that render asynchronously", async () => {
    const fields = mountCombobox(["Canada", "United States"], { openDelayMs: 60 });
    const outcome = await fillField(field(fields, "country"), "United States");
    expect(outcome.status).toBe("filled");
  });

  it("reports DROPDOWN_OPEN_FAILED (not success) when the control never opens", async () => {
    // A control that ignores every interaction — the menu never appears.
    document.body.innerHTML = `
      <form>
        <label id="c-label" for="c">Country</label>
        <div id="c" role="combobox" aria-labelledby="c-label" aria-expanded="false" aria-controls="c-list"></div>
        <ul id="c-list" role="listbox"></ul>
      </form>`;
    const fields = discoverFields(document.querySelector("form")!);
    const outcome = await fillField(field(fields, "c"), "United States");
    expect(outcome.status).toBe("review_required");
    expect(outcome.dropdown?.reasonCode).toBe("DROPDOWN_OPEN_FAILED");
  });

  it("never reports filled when the option click does not change component state", async () => {
    // Menu opens, options render — but activating one commits nothing (an inert
    // list). This is exactly the Samsara failure: a click attempt is not success.
    document.body.innerHTML = `
      <form>
        <label id="d-label" for="d">Country</label>
        <div id="d" role="combobox" aria-labelledby="d-label" aria-expanded="true" aria-controls="d-list"></div>
        <ul id="d-list" role="listbox">
          <li role="option">United States</li><li role="option">Canada</li>
        </ul>
      </form>`;
    const fields = discoverFields(document.querySelector("form")!);
    const outcome = await fillField(field(fields, "d"), "United States");
    expect(outcome.status).toBe("review_required");
    expect(outcome.dropdown?.reasonCode).toBe("DROPDOWN_VERIFICATION_FAILED");
    // The real options are still reported so the widget can ask the user.
    expect(outcome.dropdown?.options).toEqual(["United States", "Canada"]);
  });

  it("matches a controlled alias against the rendered option text (Yes ↔ full phrasing)", async () => {
    const fields = mountCombobox(["Yes, I will require sponsorship", "No, I will not require sponsorship"]);
    const outcome = await fillField(field(fields, "country"), "Yes");
    expect(outcome.status).toBe("filled");
    expect(document.querySelector("#country .select__value")!.textContent).toBe("Yes, I will require sponsorship");
  });

  it("selects from a React Select menu rendered in a document.body portal", async () => {
    document.body.innerHTML = "";
    mountPortalReactSelect(document, { id: "state", label: "State", options: ["Arizona", "California"] });
    const fields = discoverFields(document.querySelector("#state-form")!);
    const outcome = await fillField(field(fields, "state"), "Arizona");
    expect(outcome.status).toBe("filled");
    expect(document.querySelector("#state .select__value")!.textContent).toBe("Arizona");
  });

  it("opens a keyboard-only control via the ArrowDown fallback", async () => {
    const fields = mountCombobox(["Canada", "United States"], { keyboardOnly: true });
    const outcome = await fillField(field(fields, "country"), "United States");
    expect(outcome.status).toBe("filled");
  });

  it("retries a flaky first open and still succeeds", async () => {
    const fields = mountCombobox(["Canada", "United States"], { failOpens: 1 });
    const outcome = await fillField(field(fields, "country"), "United States");
    expect(outcome.status).toBe("filled");
  });

  it("processes dropdowns one at a time — never confuses two controls' options", async () => {
    document.body.innerHTML = `
      <form>
        <label id="a-label" for="a">Country</label>
        <div id="a" class="select__control" role="combobox" aria-labelledby="a-label" aria-expanded="false" aria-controls="a-list">
          <span class="select__value"></span></div>
        <ul id="a-list" role="listbox"></ul>
        <label id="b-label" for="b">State</label>
        <div id="b" class="select__control" role="combobox" aria-labelledby="b-label" aria-expanded="false" aria-controls="b-list">
          <span class="select__value"></span></div>
        <ul id="b-list" role="listbox"></ul>
      </form>`;
    for (const [id, opts] of [["a", ["United States", "Canada"]], ["b", ["Arizona", "California"]]] as const) {
      const control = document.getElementById(id)!;
      attachDropdownComponent({
        control, menu: document.getElementById(`${id}-list`)!, options: [...opts],
        display: control.querySelector<HTMLElement>(".select__value")!
      });
    }
    const fields = discoverFields(document.querySelector("form")!);
    const first = await fillField(field(fields, "a"), "United States");
    const second = await fillField(field(fields, "b"), "Arizona");
    expect(first.status).toBe("filled");
    expect(second.status).toBe("filled");
    expect(document.querySelector("#a .select__value")!.textContent).toBe("United States");
    expect(document.querySelector("#b .select__value")!.textContent).toBe("Arizona");
  });

  it("selects multiple options in a multi-select and verifies each", async () => {
    document.body.innerHTML = `
      <form>
        <span id="m-label">Where did you hear about us?</span>
        <div id="m" class="select__control" role="combobox" aria-labelledby="m-label"
             aria-expanded="false" aria-controls="m-list" aria-multiselectable="true">
          <span class="select__value"></span></div>
        <ul id="m-list" role="listbox" aria-multiselectable="true"></ul>
      </form>`;
    const control = document.getElementById("m")!;
    attachDropdownComponent({
      control, menu: document.getElementById("m-list")!, multiple: true,
      options: ["LinkedIn", "Instagram", "Industry event", "Other"],
      display: control.querySelector<HTMLElement>(".select__value")!
    });
    const fields = discoverFields(document.querySelector("form")!);
    const target = fields.find((f) => f.id === "m")!;
    const outcome = await fillField({ ...target, multiple: true }, ["LinkedIn", "Industry event"]);
    expect(outcome.status).toBe("filled");
    expect(document.querySelector("#m .select__value")!.textContent).toContain("LinkedIn");
    expect(document.querySelector("#m .select__value")!.textContent).toContain("Industry event");
  });
});
