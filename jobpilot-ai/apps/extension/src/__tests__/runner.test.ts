import { describe, expect, it } from "vitest";
import { clearJobPilotFields } from "../fields/fill";
import { runFill } from "../fields/runner";
import type { ApplicationSessionData } from "../types";
import { GREENHOUSE_FIXTURE, mountFixture } from "./fixtures";

function session(): ApplicationSessionData {
  return {
    sessionId: 1,
    atsType: "greenhouse",
    officialUrl: "https://boards.greenhouse.io/acme/1",
    jobTitle: "Backend Engineer",
    company: "Acme",
    unresolvedQuestions: [{ canonical_key: "gender" }],
    answers: [
      { canonical_key: "first_name", value: "Chandra", display_value: "Chandra", source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified: true },
      { canonical_key: "last_name", value: "Pandey", display_value: "Pandey", source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified: true },
      { canonical_key: "email", value: "cp@example.com", display_value: "cp@example.com", source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified: true },
      { canonical_key: "custom_motivation", value: "Acme’s backend role fits the reliable API work I enjoy.", display_value: "Acme’s backend role fits the reliable API work I enjoy.", source: "openai:gpt", confidence: 0.9, sensitive: false, requires_review: true, verified: false }
    ]
  };
}

describe("fill runner (end-to-end on a fixture)", () => {
  it("fills verified fields, flags review items, never touches sensitive fields", async () => {
    mountFixture(GREENHOUSE_FIXTURE);
    const summary = await runFill(document.querySelector("#application_form")!, session());

    expect((document.getElementById("first_name") as HTMLInputElement).value).toBe("Chandra");
    expect((document.getElementById("email") as HTMLInputElement).value).toBe("cp@example.com");
    expect(summary.filled).toBeGreaterThanOrEqual(3);

    // Sensitive gender select is never filled.
    expect((document.getElementById("gender") as HTMLSelectElement).value).toBe("");
    // Resume/cover file inputs become upload targets, not text fills.
    expect(summary.uploadTargets.map((u) => u.kind).sort()).toEqual(["cover-letter", "resume"]);
    // The GPT draft is inserted in context and remains a review item.
    expect((document.getElementById("why") as HTMLTextAreaElement).value).toContain("Acme");
    expect(summary.reviewRequired).toBeGreaterThanOrEqual(1);
    expect(summary.errors).toEqual([]);
  });

  it("clears every JobPilot-filled value, leaving the form as it was", async () => {
    mountFixture(GREENHOUSE_FIXTURE);
    await runFill(document.querySelector("#application_form")!, session());
    const cleared = clearJobPilotFields(document);
    expect(cleared).toBeGreaterThanOrEqual(3);
    expect((document.getElementById("first_name") as HTMLInputElement).value).toBe("");
    expect(document.querySelectorAll("[data-jobpilot-filled]").length).toBe(0);
  });
});
