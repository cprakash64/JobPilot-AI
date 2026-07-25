/**
 * The import-review name proposal. Mirrors apps/api/app/tests/
 * test_structured_identity.py so the split the user is shown in the browser is
 * the same one the backend would propose.
 */

import { describe, expect, it } from "vitest";
import { composeFullName, normalizeDisplayCase, suggestNameParts } from "../lib/names";

describe("suggestNameParts", () => {
  it("proposes first/middle/last for a three-token name", () => {
    const s = suggestNameParts("Chandra Prakash Pandey");
    expect(s.firstName).toBe("Chandra");
    expect(s.middleName).toBe("Prakash");
    expect(s.lastName).toBe("Pandey");
  });

  it("never marks a three-token split as certain", () => {
    // This is the split that produced last_name = "Prakash Pandey" — it is
    // offered for correction, never applied silently.
    expect(suggestNameParts("Chandra Prakash Pandey").certain).toBe(false);
    expect(suggestNameParts("Priya Sharma").certain).toBe(true);
  });

  it("normalizes an ALL-CAPS resume header", () => {
    const s = suggestNameParts("CHANDRA PRAKASH PANDEY");
    expect([s.firstName, s.middleName, s.lastName]).toEqual(["Chandra", "Prakash", "Pandey"]);
  });

  it("keeps surname particles with the family name", () => {
    const s = suggestNameParts("Juan de la Cruz");
    expect(s.firstName).toBe("Juan");
    expect(s.lastName).toBe("de la Cruz");
    expect(s.middleName).toBe("");
  });

  it("reads the 'Family, Given' convention explicitly", () => {
    const s = suggestNameParts("Pandey, Chandra Prakash");
    expect([s.firstName, s.middleName, s.lastName]).toEqual(["Chandra", "Prakash", "Pandey"]);
    expect(s.certain).toBe(true);
  });

  it("handles honorifics, suffixes, and mononyms", () => {
    const s = suggestNameParts("Dr. Chandra Prakash Pandey Jr.");
    expect([s.firstName, s.middleName, s.lastName]).toEqual(["Chandra", "Prakash", "Pandey"]);
    expect(s.suffix).toBe("Jr.");

    const mononym = suggestNameParts("Prince");
    expect(mononym.firstName).toBe("Prince");
    expect(mononym.lastName).toBe("");
  });

  it("returns empty parts for empty input", () => {
    expect(suggestNameParts("").firstName).toBe("");
    expect(suggestNameParts("   ").lastName).toBe("");
  });
});

describe("normalizeDisplayCase", () => {
  it("leaves anything the user typed themselves alone", () => {
    expect(normalizeDisplayCase("Ronald McDonald")).toBe("Ronald McDonald");
    expect(normalizeDisplayCase("Vincent van Gogh")).toBe("Vincent van Gogh");
  });

  it("title-cases all-caps input, including hyphens and apostrophes", () => {
    expect(normalizeDisplayCase("O'BRIEN")).toBe("O'Brien");
    expect(normalizeDisplayCase("SMITH-JONES")).toBe("Smith-Jones");
  });
});

describe("composeFullName", () => {
  it("joins only the parts that are present", () => {
    expect(composeFullName({ firstName: "Priya", middleName: "", lastName: "Sharma" })).toBe("Priya Sharma");
    expect(composeFullName({ firstName: "Chandra", middleName: "Prakash", lastName: "Pandey" }))
      .toBe("Chandra Prakash Pandey");
  });
});
