import { describe, expect, it } from "vitest";
import { normalizeApplicationUrl, urlsMatchForHandoff } from "../url";

describe("application URL handoff matching", () => {
  it("normalizes fragments and trailing slashes", () => {
    expect(normalizeApplicationUrl("https://jobs.example.com/roles/abc/#apply")?.href).toBe("https://jobs.example.com/roles/abc");
  });
  it("allows expected redirects inside one ATS family", () => {
    expect(urlsMatchForHandoff("https://jobs.ashbyhq.com/temporal/abc", "https://jobs.ashbyhq.com/temporal/abc/application")).toBe(true);
    expect(urlsMatchForHandoff("https://boards.greenhouse.io/acme/jobs/1", "https://job-boards.greenhouse.io/acme/jobs/1#app")).toBe(true);
  });
  it("rejects unrelated hosts and unrelated custom-employer paths", () => {
    expect(urlsMatchForHandoff("https://careers.acme.test/roles/backend-123", "https://evil.test/apply")).toBe(false);
    expect(urlsMatchForHandoff("https://careers.acme.test/roles/backend-123", "https://careers.acme.test/newsletter")).toBe(false);
  });

  // Regression: a stale handoff for one job must never attach to a
  // DIFFERENT job that merely shares an ATS family hostname — e.g. a
  // previous Temporal (Ashby) application must not match a new, unrelated
  // Ashby job just because both live on jobs.ashbyhq.com.
  it("rejects two different jobs on the same ATS family host", () => {
    expect(urlsMatchForHandoff("https://jobs.ashbyhq.com/temporal/2a3526f9", "https://jobs.ashbyhq.com/acme-corp/9f81c3e2")).toBe(false);
    expect(urlsMatchForHandoff("https://boards.greenhouse.io/affirm/jobs/1234567", "https://boards.greenhouse.io/stripe/jobs/7654321")).toBe(false);
  });

  it("still matches the same job across an ATS family redirect even with a different sub-path shape", () => {
    expect(urlsMatchForHandoff("https://jobs.ashbyhq.com/temporal/2a3526f9-abcd", "https://jobs.ashbyhq.com/temporal/2a3526f9-abcd/application?utm=1")).toBe(true);
  });
});
