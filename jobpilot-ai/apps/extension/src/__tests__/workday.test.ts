import { beforeEach, describe, expect, it } from "vitest";

import { WorkdayAdapter, findWorkdayNextControl, isWorkdayReviewPage } from "../ats/workday";
import { runFill } from "../fields/runner";
import type { ApplicationSessionData } from "../types";

function answer(canonical_key: string, value: string, sensitive = false) {
  return {
    canonical_key, value, display_value: sensitive ? "••••••••" : value,
    source: "profile", confidence: 1, sensitive, requires_review: false, verified: true
  };
}

function session(): ApplicationSessionData {
  return {
    sessionId: 1,
    atsType: "workday",
    officialUrl: "https://example.wd5.myworkdayjobs.com/en-US/jobs/job/1",
    jobTitle: "Engineer",
    company: "Example",
    answers: [
      answer("email", "candidate@example.test"),
      answer("application_account_password", "Strong-Password-123!", true),
      answer("application_account_password_confirm", "Strong-Password-123!", true),
      answer("phone", "+16028161309"),
      answer("phone_national", "6028161309")
    ],
    unresolvedQuestions: []
  };
}

describe("dedicated Workday adapter", () => {
  beforeEach(() => { document.body.innerHTML = ""; });

  it("detects Workday as a full adapter", () => {
    document.body.innerHTML = '<main data-automation-id="applicationPage"></main>';
    expect(WorkdayAdapter.detect({
      url: "https://example.wd5.myworkdayjobs.com/job/1",
      document
    }).matched).toBe(true);
  });

  it("fills account credentials but never copies the phone into Phone Extension", async () => {
    document.body.innerHTML = `
      <form aria-label="Create Account application">
        <label>Email Address <input name="email" type="email" required></label>
        <label>Password <input name="password" type="password" required></label>
        <label>Verify New Password <input name="verifyPassword" type="password" required></label>
        <label>Phone Number <input name="phone" required></label>
        <label>Phone Extension <input name="phoneExtension"></label>
      </form>`;
    const summary = await runFill(document.querySelector("form")!, session());
    expect((document.querySelector('[name="password"]') as HTMLInputElement).value).toBe("Strong-Password-123!");
    expect((document.querySelector('[name="verifyPassword"]') as HTMLInputElement).value).toBe("Strong-Password-123!");
    expect((document.querySelector('[name="phoneExtension"]') as HTMLInputElement).value).toBe("");
    expect(summary.mappings.find((item) => item.canonicalKey === "phone_extension")?.safeToAutoFill).toBe(false);
  });

  it("allows only safe page navigation and stops at Review", () => {
    document.body.innerHTML = `
      <h2>My Information</h2>
      <button>Save and Continue</button>
      <button>Submit</button>`;
    expect(findWorkdayNextControl({ url: location.href, document })?.textContent).toContain("Save and Continue");

    document.body.innerHTML = `
      <div aria-current="step">Review</div>
      <h2>Review your application</h2>
      <button>Submit</button>`;
    expect(isWorkdayReviewPage({ url: location.href, document })).toBe(true);
    expect(findWorkdayNextControl({ url: location.href, document })).toBeNull();
    expect(WorkdayAdapter.findSubmitControl({ url: location.href, document })?.textContent).toBe("Submit");
  });

  it("starts Workday through its resume-import path before the underlying Apply action", () => {
    document.body.innerHTML = `
      <button>Apply</button>
      <section role="dialog">
        <button>Autofill with Resume</button>
        <button>Apply Manually</button>
      </section>`;
    expect(findWorkdayNextControl({ url: location.href, document })?.textContent).toBe("Autofill with Resume");

    document.body.innerHTML = `<button>Apply</button><button>Submit</button>`;
    expect(findWorkdayNextControl({ url: location.href, document })?.textContent).toBe("Apply");
  });
});
