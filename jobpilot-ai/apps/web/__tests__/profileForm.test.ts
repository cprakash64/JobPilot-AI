/**
 * The normalization boundary — the fix for the Basic Info crash.
 *
 * Root cause being pinned: the profile page had TWO wire->form conversions, and
 * only one coalesced nulls. Migration 0013 started returning null for the
 * structured name columns, the un-hardened path spread them straight into form
 * state, and rendering Basic Info called `null.trim()` — taking the whole page
 * to the error boundary ("This page couldn't load").
 */

import { describe, expect, it } from "vitest";
import { composeFullName } from "../lib/names";
import { emptyProfile, normalizeProfile, profileToWire, str } from "../lib/profileForm";

/** Exactly what GET /profile returns for a freshly migrated account. */
const NULL_HEAVY_WIRE = {
  id: 1,
  user_id: 1,
  full_name: "",
  first_name: null,
  middle_name: null,
  last_name: null,
  preferred_first_name: null,
  preferred_last_name: null,
  preferred_name: null,
  name_confirmed: false,
  given_name: null,
  family_name: null,
  phone: null,
  phone_country_code: null,
  phone_country_iso2: null,
  phone_national_number: null,
  phone_e164: null,
  location_city: null,
  location_state: null,
  location_postal_code: null,
  location_country: null,
  linkedin_url: null,
  github_url: null,
  portfolio_url: null,
  work_authorization: null,
  requires_sponsorship: false,
  open_to_relocation: false,
  target_roles: [],
  target_levels: [],
  preferred_locations: [],
  remote_preference: "everything",
  skills: []
};

describe("normalizeProfile", () => {
  it("turns every null string field into an empty string", () => {
    const form = normalizeProfile(NULL_HEAVY_WIRE);
    const stringKeys = [
      "first_name", "middle_name", "last_name",
      "preferred_first_name", "preferred_last_name",
      "full_name", "phone", "location_city", "location_state",
      "location_postal_code", "linkedin_url", "github_url", "portfolio_url"
    ] as const;

    for (const key of stringKeys) {
      expect(typeof form[key], `${key} must be a string`).toBe("string");
      expect(form[key]).not.toBeNull();
    }
  });

  it("survives the exact payload that crashed Basic Info", () => {
    const form = normalizeProfile(NULL_HEAVY_WIRE);
    // The crashing call, now safe.
    expect(() =>
      composeFullName({
        firstName: form.first_name,
        middleName: form.middle_name,
        lastName: form.last_name
      })
    ).not.toThrow();
  });

  it("handles a null profile (a user with no profile row yet)", () => {
    const form = normalizeProfile(null);
    expect(form.first_name).toBe("");
    expect(form.target_roles).toEqual([]);
    expect(form.location_country).toBe("United States");
  });

  it("accepts legacy given_name/family_name aliases", () => {
    const form = normalizeProfile({ ...NULL_HEAVY_WIRE, given_name: "Priya", family_name: "Sharma" });
    expect(form.first_name).toBe("Priya");
    expect(form.last_name).toBe("Sharma");
  });

  it("never splits a legacy full_name into parts", () => {
    // A legacy profile carries only full_name. Splitting it is the original
    // bug; the parts stay blank so the user confirms the split themselves.
    const form = normalizeProfile({ ...NULL_HEAVY_WIRE, full_name: "Chandra Prakash Pandey" });
    expect(form.first_name).toBe("");
    expect(form.last_name).toBe("");
    // ...but the stored display name is still shown.
    expect(form.full_name).toBe("Chandra Prakash Pandey");
  });

  it("derives full_name from the parts when they exist", () => {
    const form = normalizeProfile({
      ...NULL_HEAVY_WIRE,
      first_name: "Chandra",
      middle_name: "Prakash",
      last_name: "Pandey",
      full_name: "stale value"
    });
    // Derived, so the display value cannot drift from the parts.
    expect(form.full_name).toBe("Chandra Prakash Pandey");
  });

  it("coerces list fields to arrays even when the wire sends null", () => {
    const form = normalizeProfile({
      ...NULL_HEAVY_WIRE,
      target_roles: null,
      skills: null,
      preferred_locations: undefined
    });
    expect(form.target_roles).toEqual([]);
    expect(form.skills).toEqual([]);
    expect(form.preferred_locations).toEqual([]);
  });

  it("gives booleans explicit values", () => {
    const form = normalizeProfile({ ...NULL_HEAVY_WIRE, requires_sponsorship: null, open_to_relocation: 1 });
    expect(form.requires_sponsorship).toBe(false);
    expect(form.open_to_relocation).toBe(true);
  });

  it("tolerates an incomplete legacy phone row", () => {
    // Raw phone present, structured columns never populated.
    const form = normalizeProfile({ ...NULL_HEAVY_WIRE, phone: "602-816-1309", phone_country_iso2: null });
    expect(form.phone).toBe("602-816-1309");
    // Falls back to a real option so the country <select> has a valid value.
    expect(form.phone_country_iso2).toBe("US");
  });

  it("falls back to a valid remote preference for an unknown value", () => {
    const form = normalizeProfile({ ...NULL_HEAVY_WIRE, remote_preference: "sometimes" });
    expect(form.remote_preference).toBe("everything");
  });

  it("prefers work_authorization_status over work_authorization", () => {
    const form = normalizeProfile({
      ...NULL_HEAVY_WIRE,
      work_authorization: "other",
      work_authorization_status: "authorized_us"
    });
    expect(form.work_authorization).toBe("authorized_us");
  });

  it("carries the account email through, since it is not on the profile record", () => {
    expect(normalizeProfile(NULL_HEAVY_WIRE, "user@example.com").email).toBe("user@example.com");
    expect(normalizeProfile(NULL_HEAVY_WIRE).email).toBe("");
  });
});

describe("str", () => {
  it("maps nullish values to the fallback and keeps real strings", () => {
    expect(str(null)).toBe("");
    expect(str(undefined)).toBe("");
    expect(str("x")).toBe("x");
    expect(str(42)).toBe("42");
    expect(str(null, "US")).toBe("US");
    // Objects/arrays are not renderable in an input.
    expect(str({})).toBe("");
  });
});

describe("profileToWire", () => {
  it("sends full_name derived from the parts", () => {
    const form = { ...emptyProfile, first_name: "Chandra", middle_name: "Prakash", last_name: "Pandey" };
    expect(profileToWire(form).full_name).toBe("Chandra Prakash Pandey");
  });

  it("round-trips through normalize without losing structured values", () => {
    const form = {
      ...emptyProfile,
      first_name: "Chandra",
      middle_name: "Prakash",
      last_name: "Pandey",
      location_postal_code: "85281",
      phone: "+16028161309"
    };
    const wire = profileToWire(form);
    const back = normalizeProfile(wire as never);
    expect(back.location_postal_code).toBe("85281");
    expect(back.phone).toBe("+16028161309");
    expect(back.full_name).toBe("Chandra Prakash Pandey");
  });

  it("sends empty optional links as null rather than empty strings", () => {
    const wire = profileToWire(emptyProfile);
    expect(wire.linkedin_url).toBeNull();
    expect(wire.github_url).toBeNull();
  });
});

describe("composeFullName", () => {
  it("tolerates null parts (defence in depth for render paths)", () => {
    expect(composeFullName({ firstName: null, middleName: null, lastName: null })).toBe("");
    expect(composeFullName({ firstName: "Ana", middleName: null, lastName: "Silva" })).toBe("Ana Silva");
  });
});
