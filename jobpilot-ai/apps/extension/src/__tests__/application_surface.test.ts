/**
 * Section A — safe application-surface activation.
 *
 * The live Airbnb page ships the "Role overview" tab selected and no Greenhouse
 * iframe at all. The application only appears after activating a control whose
 * ACCESSIBLE NAME is "Switch to application form" and whose VISIBLE TEXT is
 * "Apply Now". Autofill previously ran before that control was touched, found
 * nothing, and stored a terminal failure.
 *
 * Activation only reveals the form. Anything that could submit, authenticate or
 * transmit data must never be eligible.
 */

import { describe, expect, it } from "vitest";
import {
  accessibleName,
  findActivationCandidates,
  isForbiddenControl,
  selectActivationControl
} from "../ats/applicationSurface";

function mount(html: string): Document {
  document.documentElement.innerHTML = `<head></head><body>${html}</body>`;
  return document;
}

/** The confirmed live Airbnb control: aria-label differs from visible text. */
const AIRBNB_APPLY = `
  <div role="tablist" aria-label="Job information">
    <button role="tab" aria-selected="true">Role overview</button>
    <button role="tab" aria-selected="false" aria-label="Switch to application form">Apply Now</button>
  </div>`;

describe("accessible name", () => {
  it("prefers aria-label over visible text", () => {
    mount(AIRBNB_APPLY);
    const button = document.querySelector('[aria-label="Switch to application form"]')!;
    expect(accessibleName(button)).toBe("Switch to application form");
    expect(button.textContent).toBe("Apply Now");
  });

  it("resolves aria-labelledby", () => {
    mount(`<span id="lbl">Switch to application form</span><button aria-labelledby="lbl">Go</button>`);
    expect(accessibleName(document.querySelector("button")!)).toBe("Switch to application form");
  });
});

describe("selecting the activation control", () => {
  it("finds the live Airbnb 'Switch to application form' button", () => {
    mount(AIRBNB_APPLY);
    const chosen = selectActivationControl(document);
    expect(chosen).not.toBeNull();
    expect(chosen!.element.textContent).toBe("Apply Now");
    expect(chosen!.reason).toBe("accessible_name_switches_to_application_form");
  });

  it("finds a tab literally named Application", () => {
    mount(`
      <div role="tablist">
        <button role="tab" aria-selected="true">Role overview</button>
        <button role="tab" aria-selected="false">Application</button>
      </div>`);
    expect(selectActivationControl(document)!.reason).toBe("tab_named_application");
  });

  it("returns null when the page offers no surface control", () => {
    mount(`<main><h1>Software Engineer</h1><p>About the role…</p></main>`);
    expect(selectActivationControl(document)).toBeNull();
  });

  it("refuses to choose between two equally strong candidates", () => {
    mount(`
      <button aria-label="Switch to application form">Apply Now</button>
      <button aria-label="Switch to application form">Apply</button>`);
    // Guessing could navigate somewhere the user did not ask to go.
    expect(selectActivationControl(document)).toBeNull();
  });
});

describe("forbidden controls are never activated", () => {
  const forbidden = [
    ["Submit application", "<button>Submit application</button>"],
    ["Quick Apply with MyGreenhouse", "<button>Quick Apply with MyGreenhouse</button>"],
    ["Sign in", "<button>Sign in</button>"],
    ["Log in", "<button>Log in</button>"],
    ["Continue with Google", "<button>Continue with Google</button>"],
    ["Attach", "<button>Attach</button>"],
    ["Upload resume", "<button>Upload resume</button>"],
    ["I agree", "<button>I agree</button>"],
    ["Accept cookies", "<button>Accept cookies</button>"]
  ] as const;

  for (const [label, html] of forbidden) {
    it(`never activates "${label}"`, () => {
      mount(html);
      expect(isForbiddenControl(document.querySelector("button")!)).toBe(true);
      expect(selectActivationControl(document)).toBeNull();
    });
  }

  it("never activates a Submit button even inside a tablist", () => {
    mount(`<div role="tablist"><button role="tab">Submit application</button></div>`);
    expect(selectActivationControl(document)).toBeNull();
  });
});

describe("the word 'Apply' alone is not enough", () => {
  it("ignores 'Apply filters'", () => {
    mount(`<button>Apply filters</button>`);
    expect(selectActivationControl(document)).toBeNull();
  });

  it("ignores a bare marketing 'Apply Now' with no navigation semantics", () => {
    // Not a tab, no aria-controls, no anchor target — could be anything.
    mount(`<button>Apply Now</button>`);
    expect(selectActivationControl(document)).toBeNull();
  });

  it("accepts 'Apply Now' when it is a tab", () => {
    mount(`<div role="tablist"><button role="tab">Apply Now</button></div>`);
    expect(selectActivationControl(document)!.reason).toBe("apply_control_in_tablist_or_anchor");
  });

  it("accepts 'Apply' when it controls another element", () => {
    mount(`<button aria-controls="app-panel">Apply</button><div id="app-panel"></div>`);
    expect(selectActivationControl(document)).not.toBeNull();
  });

  it("ignores 'Applied' and 'Apply for other roles'", () => {
    mount(`<button role="tab">Applied</button><button role="tab">Apply for other roles</button>`);
    expect(selectActivationControl(document)).toBeNull();
  });
});

describe("hidden and disabled controls", () => {
  it("ignores a hidden activation control", () => {
    mount(`<button aria-label="Switch to application form" style="display:none">Apply Now</button>`);
    expect(selectActivationControl(document)).toBeNull();
  });

  it("ignores a disabled activation control", () => {
    mount(`<button aria-label="Switch to application form" disabled>Apply Now</button>`);
    expect(selectActivationControl(document)).toBeNull();
  });

  it("ignores JobPilot's own widget buttons", () => {
    mount(`<div id="jobpilot-assisted-apply"><button aria-label="Switch to application form">Apply Now</button></div>`);
    expect(selectActivationControl(document)).toBeNull();
  });
});

describe("candidate ordering", () => {
  it("prefers the explicit 'switch to application form' control over a generic tab", () => {
    mount(`
      <div role="tablist">
        <button role="tab">Application</button>
        <button role="tab" aria-label="Switch to application form">Apply Now</button>
      </div>`);
    const [best] = findActivationCandidates(document);
    expect(best.reason).toBe("accessible_name_switches_to_application_form");
  });
});
