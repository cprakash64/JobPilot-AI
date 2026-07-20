/**
 * Section A — reserved example domains are not usable application addresses.
 * Mirrors app/tests/test_application_email.py so client and server agree.
 */

import { describe, expect, it } from "vitest";
import {
  RESERVED_EMAIL_MESSAGE,
  isReservedEmailDomain,
  validateApplicationEmail
} from "../lib/emailValidation";

describe("reserved domains", () => {
  const reserved = [
    "someone@example.com", "someone@example.net", "someone@example.org",
    "someone@example.edu", "someone@mail.example.com",
    "someone@careers.jobs.example.org", "someone@EXAMPLE.COM",
    "  someone@Example.Net  ", "someone@host.invalid", "someone@localhost"
  ];
  for (const address of reserved) {
    it(`rejects ${address.trim()}`, () => {
      expect(isReservedEmailDomain(address)).toBe(true);
      expect(validateApplicationEmail(address)).toBe(RESERVED_EMAIL_MESSAGE);
    });
  }

  const real = [
    "someone@gmail.com", "someone@company.co.uk",
    // Containing "example" is not the same as BEING a reserved domain.
    "someone@notexample.com", "someone@myexample.org", "someone@example.company.com"
  ];
  for (const address of real) {
    it(`accepts ${address}`, () => {
      expect(isReservedEmailDomain(address)).toBe(false);
      expect(validateApplicationEmail(address)).toBeNull();
    });
  }
});

describe("format validation", () => {
  it("rejects malformed addresses", () => {
    for (const bad of ["notanemail", "@nolocal.com", "nodomain@", "a b@c.com"]) {
      expect(validateApplicationEmail(bad)).toBe("Enter a valid email address.");
    }
  });

  it("treats an empty field as not-an-error (readiness reports it instead)", () => {
    expect(validateApplicationEmail("")).toBeNull();
    expect(validateApplicationEmail(null)).toBeNull();
    expect(validateApplicationEmail(undefined)).toBeNull();
  });

  it("shows the actionable message, not a technical one", () => {
    expect(RESERVED_EMAIL_MESSAGE).toContain("real email address");
    expect(RESERVED_EMAIL_MESSAGE).toContain("employer");
  });
});
