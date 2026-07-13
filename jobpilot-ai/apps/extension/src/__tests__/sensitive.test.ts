import { describe, expect, it } from "vitest";
import { detectSensitive, isSensitiveKey } from "../fields/sensitive";

describe("sensitive detection", () => {
  it.each([
    ["Gender", "gender"],
    ["What is your race?", "race"],
    ["Are you Hispanic or Latino?", "ethnicity"],
    ["Do you have a disability?", "disability_status"],
    ["Protected Veteran Status", "veteran_status"],
    ["Have you ever been convicted of a felony?", "criminal_history"],
    ["What is your current salary?", "salary_history"],
    ["Do you require a security clearance?", "security_clearance"],
    ["I certify that the information is accurate", "legal_attestation"],
    ["Voluntary Self-Identification of Disability", "disability_status"]
  ])("classifies %s as sensitive", (text, key) => {
    const result = detectSensitive(text);
    expect(result.sensitive).toBe(true);
    expect(result.key).toBe(key);
    expect(isSensitiveKey(result.key!)).toBe(true);
  });

  it("does not flag ordinary fields", () => {
    for (const text of ["First name", "Email address", "LinkedIn profile", "Years of experience"]) {
      expect(detectSensitive(text).sensitive).toBe(false);
    }
  });
});
