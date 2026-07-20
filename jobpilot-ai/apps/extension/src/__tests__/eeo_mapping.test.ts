/**
 * Section E — mapping stored EEO answers onto real ATS option vocabularies.
 *
 * Every case here must fail CLOSED. A demographic answer that cannot be matched
 * to exactly one option with the same meaning becomes needs_confirmation for
 * the user to answer on the page — never a plausible-looking guess.
 *
 * Fixtures use the option strings Greenhouse and Workday actually render.
 */

import { describe, expect, it } from "vitest";
import { mapEeoAnswer, normalizeOption } from "../fields/eeoMapping";

// --- real ATS vocabularies -------------------------------------------------- //
const GREENHOUSE = {
  gender: ["Male", "Female", "Decline To Self Identify"],
  veteran: [
    "I identify as one or more of the classifications of a protected veteran",
    "I am not a protected veteran",
    "I don't wish to answer"
  ],
  disability: [
    "Yes, I have a disability, or have had one in the past",
    "No, I do not have a disability and have not had one in the past",
    "I do not want to answer"
  ],
  race: [
    "American Indian or Alaska Native",
    "Asian",
    "Black or African American",
    "Native Hawaiian or Other Pacific Islander",
    "White",
    "Two or More Races",
    "Decline To Self Identify"
  ]
};

const WORKDAY = {
  gender: ["Man", "Woman", "Non-Binary", "Prefer Not to Say"],
  hispanic: ["Yes, Hispanic or Latino", "No, not Hispanic or Latino", "Prefer Not to Say"]
};

const consented = { consentActive: true };

describe("option normalization", () => {
  it("ignores case, punctuation and curly apostrophes", () => {
    expect(normalizeOption("I don’t wish to answer")).toBe("i dont wish to answer");
    expect(normalizeOption("Black or African American")).toBe("black or african american");
    expect(normalizeOption("  Asian  ")).toBe("asian");
  });
});

describe("consent and stored-answer gating", () => {
  it("never fills when the user stored no answer", () => {
    const result = mapEeoAnswer({
      field: "gender_identity", storedValues: [], options: WORKDAY.gender, ...consented
    });
    expect(result).toEqual({ status: "needs_confirmation", reason: "NO_STORED_ANSWER" });
  });

  it("never fills when consent is not active, even with a stored answer", () => {
    const result = mapEeoAnswer({
      field: "gender_identity", storedValues: ["woman"], options: WORKDAY.gender, consentActive: false
    });
    expect(result).toEqual({ status: "needs_confirmation", reason: "NO_CONSENT" });
  });
});

describe("gender identity", () => {
  it("maps onto an explicit identity vocabulary", () => {
    const result = mapEeoAnswer({
      field: "gender_identity", storedValues: ["non_binary"], options: WORKDAY.gender, ...consented
    });
    expect(result).toEqual({ status: "fill", values: ["Non-Binary"] });
  });

  it("does NOT map an identity onto a binary sex question by default", () => {
    // Greenhouse offers only Male/Female. Those are sex terms; mapping a gender
    // identity onto them is a product decision, not a default.
    const result = mapEeoAnswer({
      field: "gender_identity", storedValues: ["woman"], options: GREENHOUSE.gender, ...consented
    });
    expect(result).toEqual({ status: "needs_confirmation", reason: "NO_MATCHING_OPTION" });
  });

  it("maps onto a binary sex question only when explicitly permitted", () => {
    const result = mapEeoAnswer({
      field: "gender_identity",
      storedValues: ["man"],
      options: GREENHOUSE.gender,
      allowBinarySexMapping: true,
      ...consented
    });
    expect(result).toEqual({ status: "fill", values: ["Male"] });
  });

  it("maps prefer_not_to_answer onto 'Decline To Self Identify'", () => {
    const result = mapEeoAnswer({
      field: "gender_identity", storedValues: ["prefer_not_to_answer"], options: GREENHOUSE.gender, ...consented
    });
    expect(result).toEqual({ status: "fill", values: ["Decline To Self Identify"] });
  });
});

describe("veteran status", () => {
  it("maps a protected-veteran answer to the matching long-form option", () => {
    const result = mapEeoAnswer({
      field: "veteran_status", storedValues: ["protected_veteran"], options: GREENHOUSE.veteran, ...consented
    });
    expect(result).toEqual({
      status: "fill",
      values: ["I identify as one or more of the classifications of a protected veteran"]
    });
  });

  it("never infers 'not a protected veteran' from 'not a veteran'", () => {
    // Greenhouse offers no "I am not a veteran" option. These are different
    // statements and one must not stand in for the other.
    const result = mapEeoAnswer({
      field: "veteran_status", storedValues: ["not_a_veteran"], options: GREENHOUSE.veteran, ...consented
    });
    expect(result).toEqual({ status: "needs_confirmation", reason: "NO_MATCHING_OPTION" });
  });
});

describe("disability status", () => {
  it("maps to the full EEOC sentence", () => {
    const result = mapEeoAnswer({
      field: "disability_status", storedValues: ["no"], options: GREENHOUSE.disability, ...consented
    });
    expect(result).toEqual({
      status: "fill",
      values: ["No, I do not have a disability and have not had one in the past"]
    });
  });

  it("asks when the control offers no compatible option", () => {
    const result = mapEeoAnswer({
      field: "disability_status", storedValues: ["yes"], options: ["Agree", "Disagree"], ...consented
    });
    expect(result.status).toBe("needs_confirmation");
  });
});

describe("hispanic or latino", () => {
  it("maps onto a qualified yes/no vocabulary", () => {
    const result = mapEeoAnswer({
      field: "hispanic_or_latino", storedValues: ["no"], options: WORKDAY.hispanic, ...consented
    });
    expect(result).toEqual({ status: "fill", values: ["No, not Hispanic or Latino"] });
  });
});

describe("race and ethnicity", () => {
  it("fills several selections when the control is multi-select", () => {
    const result = mapEeoAnswer({
      field: "race_ethnicity",
      storedValues: ["asian", "white"],
      options: GREENHOUSE.race,
      multiSelect: true,
      ...consented
    });
    expect(result).toEqual({ status: "fill", values: ["Asian", "White"] });
  });

  it("NEVER reduces multiple races to one in a single-select control", () => {
    const result = mapEeoAnswer({
      field: "race_ethnicity",
      storedValues: ["asian", "white"],
      options: GREENHOUSE.race,
      multiSelect: false,
      ...consented
    });
    expect(result).toEqual({
      status: "needs_confirmation",
      reason: "MULTI_VALUE_IN_SINGLE_SELECT"
    });
  });

  it("fills a single stored race in a single-select control", () => {
    const result = mapEeoAnswer({
      field: "race_ethnicity", storedValues: ["black_or_african_american"], options: GREENHOUSE.race, ...consented
    });
    expect(result).toEqual({ status: "fill", values: ["Black or African American"] });
  });

  it("asks when one stored race has no equivalent option", () => {
    const result = mapEeoAnswer({
      field: "race_ethnicity",
      storedValues: ["asian", "native_hawaiian_or_other_pacific_islander"],
      options: ["Asian", "White"],
      multiSelect: true,
      ...consented
    });
    expect(result).toEqual({ status: "needs_confirmation", reason: "NO_MATCHING_OPTION" });
  });

  it("asks when two options match the same stored value", () => {
    const result = mapEeoAnswer({
      field: "race_ethnicity",
      storedValues: ["another_race_or_ethnicity"],
      // Both "Two or More Races" and "Other" map to the same canonical value.
      options: ["Two or More Races", "Other", "Asian"],
      ...consented
    });
    expect(result).toEqual({ status: "needs_confirmation", reason: "AMBIGUOUS_OPTIONS" });
  });

  it("asks when the control renders no options at all", () => {
    const result = mapEeoAnswer({
      field: "race_ethnicity", storedValues: ["asian"], options: [], ...consented
    });
    expect(result.status).toBe("needs_confirmation");
  });
});
