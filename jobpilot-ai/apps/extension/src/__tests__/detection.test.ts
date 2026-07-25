import { describe, expect, it } from "vitest";
import { detectAdapter } from "../ats/registry";
import { ASHBY_FIXTURE, GENERIC_FIXTURE, GREENHOUSE_FIXTURE, LEVER_FIXTURE, mountFixture } from "./fixtures";

function ctx(url: string, html: string) {
  return { url, document: mountFixture(html) };
}

describe("ATS detection", () => {
  it("detects Greenhouse by host and DOM signature", () => {
    const out = detectAdapter(ctx("https://boards.greenhouse.io/acme/jobs/1", GREENHOUSE_FIXTURE));
    expect(out?.adapter.id).toBe("greenhouse");
    expect(out?.result.confidence).toBeGreaterThanOrEqual(0.5);
    expect(out?.limited).toBe(false);
  });

  it("detects Lever", () => {
    const out = detectAdapter(ctx("https://jobs.lever.co/acme/abc", LEVER_FIXTURE));
    expect(out?.adapter.id).toBe("lever");
  });

  it("detects Ashby", () => {
    const out = detectAdapter(ctx("https://jobs.ashbyhq.com/acme/xyz", ASHBY_FIXTURE));
    expect(out?.adapter.id).toBe("ashby");
  });

  it("falls back to the generic adapter for a plain form", () => {
    const out = detectAdapter(ctx("https://careers.example-co.com/apply", GENERIC_FIXTURE));
    expect(out?.adapter.id).toBe("generic");
  });

  it("uses the dedicated full-support Workday adapter", () => {
    const out = detectAdapter(ctx("https://acme.wd1.myworkdayjobs.com/careers", GENERIC_FIXTURE));
    expect(out?.result.atsId).toBe("workday");
    expect(out?.adapter.id).toBe("workday");
    expect(out?.limited).toBe(false);
  });

  it("returns null when there is no form", () => {
    expect(detectAdapter(ctx("https://example.com", "<div>no form here</div>"))).toBeNull();
  });
});
