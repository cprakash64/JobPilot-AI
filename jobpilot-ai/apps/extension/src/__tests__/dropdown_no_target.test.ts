/**
 * Section E — never touch a dropdown we have no answer for.
 *
 * The live symptom: on the Airbnb/Greenhouse application every custom dropdown
 * was left OPEN and still reading "Select…". The cause was fillDropdown probing
 * unanswered controls — opening each menu purely to enumerate its options for
 * the review widget. That is useful on demand, but during an autofill run it
 * visibly disturbs the employer's page and looks like a broken fill.
 */

import { describe, expect, it } from "vitest";
import { discoverFields } from "../fields/discovery";
import { fillDropdown } from "../fields/dropdown";

function mountSelect(): ReturnType<typeof discoverFields>[number] {
  document.body.innerHTML = `
    <form>
      <label for="src">How did you hear about this job?</label>
      <select id="src">
        <option value="">Select…</option>
        <option value="linkedin">LinkedIn</option>
        <option value="referral">Referral</option>
      </select>
    </form>`;
  const fields = discoverFields(document.querySelector("form")!);
  return fields.find((f) => f.id === "src")!;
}

/** An ARIA combobox, matching the live Greenhouse control shape. */
function mountCombobox(): ReturnType<typeof discoverFields>[number] {
  document.body.innerHTML = `
    <form>
      <label id="lbl">Country</label>
      <div
        id="country" role="combobox" tabindex="0"
        aria-labelledby="lbl" aria-expanded="false" aria-controls="country-listbox"
      >Select…</div>
      <ul id="country-listbox" role="listbox" hidden>
        <li role="option">United States +1</li>
        <li role="option">Canada +1</li>
      </ul>
    </form>`;
  const fields = discoverFields(document.querySelector("form")!);
  return fields.find((f) => f.id === "country")!;
}

describe("no answer means no interaction", () => {
  it("returns SKIPPED_NO_TARGET instead of opening a native select", async () => {
    const field = mountSelect();
    const result = await fillDropdown(field, { values: [] });

    expect(result.ok).toBe(false);
    expect(result.reason).toBe("SKIPPED_NO_TARGET");
    // It did not enumerate options, because it never opened the control.
    expect(result.options).toEqual([]);
  });

  it("leaves aria-expanded false on an unanswered combobox", async () => {
    const field = mountCombobox();
    const result = await fillDropdown(field, { values: [] });

    expect(result.reason).toBe("SKIPPED_NO_TARGET");
    // The exact live regression: the menu must stay shut.
    expect(document.getElementById("country")!.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById("country-listbox")!.hasAttribute("hidden")).toBe(true);
  });

  it("treats whitespace-only and empty-string answers as no answer", async () => {
    for (const values of [[], [""], ["   "], ["", "  "]]) {
      const field = mountCombobox();
      const result = await fillDropdown(field, { values });
      expect(result.reason, `values=${JSON.stringify(values)}`).toBe("SKIPPED_NO_TARGET");
      expect(document.getElementById("country")!.getAttribute("aria-expanded")).toBe("false");
    }
  });

  it("does not disturb the control's existing displayed value", async () => {
    const field = mountCombobox();
    await fillDropdown(field, { values: [] });
    expect(document.getElementById("country")!.textContent).toBe("Select…");
  });

  it("still opens the control when probing is explicitly requested", async () => {
    // The review widget's "show me the options" action — a user action, not
    // part of an autofill run.
    const field = mountSelect();
    const result = await fillDropdown(field, { values: [], allowProbe: true });

    expect(result.reason).toBe("ANSWER_MISSING");
    expect(result.options).toContain("LinkedIn");
    expect(result.options).toContain("Referral");
  });

  it("still fills normally when an answer IS supplied", async () => {
    const field = mountSelect();
    const result = await fillDropdown(field, { values: ["LinkedIn"] });

    expect(result.ok).toBe(true);
    expect((document.getElementById("src") as HTMLSelectElement).value).toBe("linkedin");
  });
});
