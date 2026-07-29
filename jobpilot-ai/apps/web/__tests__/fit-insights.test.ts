import { describe, expect, it } from "vitest";
import { buildFitInsights } from "../lib/fitInsights";
import type { Job } from "../lib/api";

/**
 * The Overview coaches from real match data or says nothing. These tests pin
 * the boundary: every strength, gap and suggestion must be traceable to a field
 * the backend returned.
 */

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    title: "Machine Learning Engineer",
    company: "Acme",
    source: "greenhouse",
    location: "Remote",
    workplace_type: "remote",
    employment_type: "full-time",
    seniority_level: "mid",
    posted_at: null,
    discovered_at: "2026-07-25T12:00:00Z",
    application_url: "https://job-boards.greenhouse.io/acme/1",
    source_url: "https://job-boards.greenhouse.io/acme/1",
    description_clean: "Short description.",
    required_skills: ["Python", "PyTorch", "Kubernetes"],
    preferred_skills: [],
    responsibilities: [],
    match: {
      fit_score: 82,
      fit_label: "Strong fit",
      score_state: "scored",
      match_reasons: ["Title aligns with your target role"],
      missing_skills: ["Kubernetes"],
      risk_factors: ["Posting mentions an on-call rotation."],
      recommended_resume_angle: "Lead with production ML systems.",
      confidence: 0.8,
      explanation_source: "deterministic"
    },
    ...overrides
  } as Job;
}

describe("buildFitInsights", () => {
  it("splits required skills into matched and missing without inventing either", () => {
    const insights = buildFitInsights(job());
    expect(insights.matchedSkills).toEqual(["Python", "PyTorch"]);
    expect(insights.missingSkills).toEqual(["Kubernetes"]);
    expect(insights.strengths).toEqual(["Title aligns with your target role"]);
    expect(insights.risks).toEqual(["Posting mentions an on-call rotation."]);
  });

  it("matches skill names case-insensitively", () => {
    const insights = buildFitInsights(
      job({
        required_skills: ["Kubernetes", "python"],
        match: { ...job().match!, missing_skills: ["KUBERNETES"] }
      })
    );
    expect(insights.matchedSkills).toEqual(["python"]);
  });

  it("turns each real signal into one practical suggestion", () => {
    const insights = buildFitInsights(job());
    const ids = insights.suggestions.map((suggestion) => suggestion.id);
    expect(ids).toContain("close-gaps");
    expect(ids).toContain("risk-0");
    expect(ids).toContain("angle");
    expect(ids).toContain("foreground-matches");
    // Named gaps appear verbatim; nothing else is asserted about them.
    expect(insights.suggestions.find((s) => s.id === "close-gaps")?.title).toContain("Kubernetes");
    expect(insights.suggestions.find((s) => s.id === "angle")?.body).toBe(
      "Lead with production ML systems."
    );
  });

  it("promises no score change anywhere in its copy", () => {
    const insights = buildFitInsights(job());
    for (const suggestion of insights.suggestions) {
      expect(`${suggestion.title} ${suggestion.body}`).not.toMatch(
        /raise your score|increase your score|guarantee|will improve your fit score/i
      );
    }
  });

  it("reports no signals rather than filler when the match is missing", () => {
    const insights = buildFitInsights(job({ match: null, required_skills: [] }));
    expect(insights.hasSignals).toBe(false);
    expect(insights.strengths).toEqual([]);
    expect(insights.missingSkills).toEqual([]);
    expect(insights.suggestions).toEqual([]);
  });

  it("keeps the description suggestion only when there is a description to read", () => {
    const long = "word ".repeat(120);
    expect(
      buildFitInsights(job({ description_clean: long })).suggestions.map((s) => s.id)
    ).toContain("mirror-language");
    expect(
      buildFitInsights(job({ description_clean: "Too short." })).suggestions.map((s) => s.id)
    ).not.toContain("mirror-language");
  });

  it("drops duplicate and blank values from every list", () => {
    const insights = buildFitInsights(
      job({
        required_skills: ["Python", "Python", "  "],
        match: {
          ...job().match!,
          match_reasons: ["Same reason", "Same reason"],
          missing_skills: [],
          risk_factors: []
        }
      })
    );
    expect(insights.matchedSkills).toEqual(["Python"]);
    expect(insights.strengths).toEqual(["Same reason"]);
  });
});
