import { describe, expect, it } from "vitest";
import { discoverFields } from "../fields/discovery";
import { GREENHOUSE_FIXTURE, mountFixture } from "./fixtures";

describe("field discovery", () => {
  it("discovers labeled fields and skips honeypot + disabled fields", () => {
    mountFixture(GREENHOUSE_FIXTURE);
    const fields = discoverFields(document.querySelector("#application_form")!);
    const byId = new Map(fields.map((f) => [f.id, f]));

    expect(byId.has("first_name")).toBe(true);
    expect(byId.get("first_name")!.label).toContain("First Name");
    expect(byId.get("first_name")!.required).toBe(true);
    expect(byId.get("email")!.autocomplete).toBe("email");

    // File inputs discovered as file controls.
    expect(byId.get("resume")!.control).toBe("file");
    // Select captures its options.
    expect(byId.get("work_auth")!.control).toBe("select");
    expect(byId.get("work_auth")!.options).toContain("Yes");

    // Honeypot (off-screen) and disabled fields are excluded.
    expect(fields.some((f) => f.name.includes("honeypot"))).toBe(false);
    expect(byId.has("disabled_field")).toBe(false);
  });

  it("captures section headings for sensitive groups", () => {
    mountFixture(GREENHOUSE_FIXTURE);
    const fields = discoverFields(document.querySelector("#application_form")!);
    const gender = fields.find((f) => f.id === "gender")!;
    expect(gender.sectionHeading).toMatch(/Voluntary Self-Identification/i);
  });
});
