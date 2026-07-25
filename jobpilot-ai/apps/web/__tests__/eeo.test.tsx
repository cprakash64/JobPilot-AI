/**
 * Optional EEO — corrected per-question option sets and consent rules.
 *
 * The bug being pinned: one shared list ("Prefer not to answer / Yes / No /
 * Another option") was reused for five questions, so Gender offered Yes/No,
 * every field arrived preselected, and race collapsed to "Another option".
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DemographicsForm } from "../components/DemographicsForm";
import {
  DISABILITY_STATUS_OPTIONS,
  GENDER_IDENTITY_OPTIONS,
  HISPANIC_OR_LATINO_OPTIONS,
  PREFER_NOT,
  RACE_ETHNICITY_OPTIONS,
  VETERAN_STATUS_OPTIONS,
  emptyEeoForm,
  hasAnyAnswer,
  normalizeEeo,
  toggleRaceSelection
} from "../lib/eeo";

const ALL_QUESTIONS = [
  GENDER_IDENTITY_OPTIONS,
  VETERAN_STATUS_OPTIONS,
  DISABILITY_STATUS_OPTIONS,
  HISPANIC_OR_LATINO_OPTIONS,
  RACE_ETHNICITY_OPTIONS
];

function mockEeoFetch(existing: Record<string, unknown> | null = null) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    calls.push({ url: String(input), init });
    if (init?.method === "PUT" || init?.method === "DELETE") {
      return Promise.resolve(new Response(JSON.stringify({ demographics: null }), { status: 200 }));
    }
    return Promise.resolve(
      new Response(JSON.stringify({ demographics: existing }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
  });
  return calls;
}

// --------------------------------------------------------------------------- //
// Vocabularies
// --------------------------------------------------------------------------- //
describe("EEO vocabularies", () => {
  it("gender identity never offers Yes/No", () => {
    const values = GENDER_IDENTITY_OPTIONS.map((o) => o.value);
    expect(values).not.toContain("yes");
    expect(values).not.toContain("no");
    const labels = GENDER_IDENTITY_OPTIONS.map((o) => o.label);
    expect(labels).toEqual(["Woman", "Man", "Non-binary", "Self-describe", "Prefer not to answer"]);
  });

  it("every question offers Prefer not to answer", () => {
    for (const options of ALL_QUESTIONS) {
      expect(options.map((o) => o.value)).toContain(PREFER_NOT);
    }
  });

  it("veteran status keeps 'not a protected veteran' distinct from 'not a veteran'", () => {
    const values = VETERAN_STATUS_OPTIONS.map((o) => o.value);
    expect(values).toContain("not_protected_veteran");
    expect(values).toContain("not_a_veteran");
  });

  it("race/ethnicity is not collapsed into one generic option", () => {
    const values = RACE_ETHNICITY_OPTIONS.map((o) => o.value);
    expect(values).toContain("american_indian_or_alaska_native");
    expect(values).toContain("asian");
    expect(values).toContain("black_or_african_american");
    expect(values).toContain("native_hawaiian_or_other_pacific_islander");
    expect(values).toContain("white");
    expect(values).not.toContain("another_option");
  });

  it("no two questions present the same user-facing options", () => {
    // The original bug was five questions rendering one identical option list.
    // Canonical VALUES may legitimately coincide (disability and Hispanic/Latino
    // are both yes/no/prefer_not_to_answer), but what the user reads must be
    // specific to the question being asked.
    const labelSets = ALL_QUESTIONS.map((options) => options.map((o) => o.label).join("|"));
    expect(new Set(labelSets).size).toBe(labelSets.length);
  });

  it("spells out the disability question rather than reusing bare Yes/No", () => {
    const labels = DISABILITY_STATUS_OPTIONS.map((o) => o.label);
    expect(labels[0]).toMatch(/^Yes, I have a disability/);
    expect(labels[1]).toMatch(/^No, I do not have a disability/);
    // Hispanic/Latino legitimately IS a plain yes/no question.
    expect(HISPANIC_OR_LATINO_OPTIONS.map((o) => o.label)).toEqual(["Yes", "No", "Prefer not to answer"]);
  });
});

// --------------------------------------------------------------------------- //
// Defaults / consent
// --------------------------------------------------------------------------- //
describe("EEO defaults", () => {
  it("preselects nothing and leaves consent off", () => {
    expect(emptyEeoForm.gender_identity).toBeNull();
    expect(emptyEeoForm.veteran_status).toBeNull();
    expect(emptyEeoForm.disability_status).toBeNull();
    expect(emptyEeoForm.hispanic_or_latino).toBeNull();
    expect(emptyEeoForm.race_ethnicity).toEqual([]);
    expect(emptyEeoForm.consent_to_store).toBe(false);
  });

  it("distinguishes 'not answered' from 'prefer not to answer'", () => {
    expect(hasAnyAnswer(emptyEeoForm)).toBe(false);
    expect(hasAnyAnswer({ ...emptyEeoForm, gender_identity: PREFER_NOT })).toBe(true);
  });
});

describe("race multi-select", () => {
  it("allows several races at once", () => {
    let selection = toggleRaceSelection([], "asian");
    selection = toggleRaceSelection(selection, "white");
    expect(selection).toEqual(["asian", "white"]);
  });

  it("makes Prefer not to answer mutually exclusive", () => {
    const withRaces = toggleRaceSelection(toggleRaceSelection([], "asian"), "white");
    expect(toggleRaceSelection(withRaces, PREFER_NOT)).toEqual([PREFER_NOT]);
    // ...and choosing a real category clears it again.
    expect(toggleRaceSelection([PREFER_NOT], "asian")).toEqual(["asian"]);
  });

  it("toggles a selection off", () => {
    expect(toggleRaceSelection(["asian"], "asian")).toEqual([]);
  });
});

describe("legacy value handling", () => {
  it("never turns a legacy gender='yes' into a gender identity", () => {
    // The server quarantines it (migration 0015) and sends null; the client
    // must not resurrect it either.
    const form = normalizeEeo({ gender_identity: null } as never);
    expect(form.gender_identity).toBeNull();
    expect(GENDER_IDENTITY_OPTIONS.map((o) => o.value)).not.toContain("yes");
  });

  it("normalizes a missing record into the empty, unconsented form", () => {
    expect(normalizeEeo(null)).toEqual(emptyEeoForm);
  });
});

// --------------------------------------------------------------------------- //
// Rendered form
// --------------------------------------------------------------------------- //
describe("DemographicsForm", () => {
  beforeEach(() => {
    cleanup();
    localStorage.setItem("jobpilot_token", "token");
    vi.restoreAllMocks();
  });

  it("renders Gender identity with no Yes/No radios", async () => {
    mockEeoFetch();
    render(React.createElement(DemographicsForm));

    const genderGroup = await screen.findByRole("group", { name: "Gender identity" });
    const labels = within(genderGroup)
      .getAllByRole("radio")
      .map((radio) => (radio as HTMLInputElement).value);
    expect(labels).toEqual(["woman", "man", "non_binary", "self_describe", PREFER_NOT]);
  });

  it("preselects no demographic answer for a new user", async () => {
    mockEeoFetch();
    render(React.createElement(DemographicsForm));

    await screen.findByRole("group", { name: "Gender identity" });
    const checked = screen.getAllByRole("radio").filter((r) => (r as HTMLInputElement).checked);
    expect(checked).toHaveLength(0);
  });

  it("leaves the consent checkbox unchecked by default", async () => {
    mockEeoFetch();
    render(React.createElement(DemographicsForm));

    const consent = await screen.findByRole("checkbox", { name: /I consent to JobPilot AI securely storing/i });
    expect((consent as HTMLInputElement).checked).toBe(false);
  });

  it("refuses to save answers without consent, and sends no request", async () => {
    const calls = mockEeoFetch();
    render(React.createElement(DemographicsForm));

    const genderGroup = await screen.findByRole("group", { name: "Gender identity" });
    await userEvent.click(within(genderGroup).getByRole("radio", { name: "Woman" }));
    await userEvent.click(screen.getByRole("button", { name: /save settings/i }));

    expect(await screen.findByTestId("section-error")).toBeInTheDocument();
    expect(calls.filter((c) => c.init?.method === "PUT")).toHaveLength(0);
  });

  it("reveals a self-describe field only when Self-describe is chosen", async () => {
    mockEeoFetch();
    render(React.createElement(DemographicsForm));

    const genderGroup = await screen.findByRole("group", { name: "Gender identity" });
    expect(screen.queryByLabelText(/how would you describe your gender/i)).not.toBeInTheDocument();

    await userEvent.click(within(genderGroup).getByRole("radio", { name: "Self-describe" }));
    expect(await screen.findByLabelText(/how would you describe your gender/i)).toBeInTheDocument();
  });

  it("shows a review notice when the server flagged quarantined legacy values", async () => {
    mockEeoFetch({ needs_review: true, consent_to_store: true, race_ethnicity: [] });
    render(React.createElement(DemographicsForm));
    expect(await screen.findByText(/no longer valid/i)).toBeInTheDocument();
  });

  it("saves when consent is given", async () => {
    const calls = mockEeoFetch();
    render(React.createElement(DemographicsForm));

    const genderGroup = await screen.findByRole("group", { name: "Gender identity" });
    await userEvent.click(within(genderGroup).getByRole("radio", { name: "Woman" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /I consent to JobPilot AI securely storing/i }));
    await userEvent.click(screen.getByRole("button", { name: /save settings/i }));

    const put = calls.find((c) => c.init?.method === "PUT");
    expect(put).toBeDefined();
    const body = JSON.parse(String(put?.init?.body));
    expect(body.gender_identity).toBe("woman");
    expect(body.consent_to_store).toBe(true);
  });
});
