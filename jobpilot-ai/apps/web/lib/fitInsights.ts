import type { Job } from "@/lib/api";

/**
 * Turns the match record into something a candidate can act on.
 *
 * Everything here is derived from fields the backend actually returns —
 * strengths from `match_reasons`, gaps from `missing_skills`, risks from
 * `risk_factors`, and the tailoring angle from `recommended_resume_angle`.
 * Nothing is inferred about the user's profile, no category sub-scores are
 * invented, and no suggestion promises a score change: the backend produces one
 * overall number and does not model what would happen if a gap were closed.
 */

export type FitSuggestion = {
  /** Stable key for React and for tests. */
  id: string;
  title: string;
  body: string;
  /** Jumps the user to the part of the workspace that acts on this. */
  action?: "materials" | "description" | "profile";
};

export type FitInsights = {
  /** Reasons the score went up, straight from the match explanation. */
  strengths: string[];
  /** Required skills this job asked for that the match did not flag as gaps. */
  matchedSkills: string[];
  /** Skills the match flagged as missing. */
  missingSkills: string[];
  /** Non-skill concerns (seniority, authorisation, on-call, …). */
  risks: string[];
  suggestions: FitSuggestion[];
  /** True when there is genuinely nothing to report rather than nothing known. */
  hasSignals: boolean;
};

function unique(values: string[]): string[] {
  return Array.from(new Set(values.filter((value) => value.trim().length > 0)));
}

export function buildFitInsights(job: Job): FitInsights {
  const match = job.match;
  const missingSkills = unique(match?.missing_skills ?? []);
  const missingLower = new Set(missingSkills.map((skill) => skill.toLowerCase()));
  const matchedSkills = unique(job.required_skills ?? []).filter(
    (skill) => !missingLower.has(skill.toLowerCase())
  );
  const strengths = unique(match?.match_reasons ?? []);
  const risks = unique(match?.risk_factors ?? []);

  const suggestions: FitSuggestion[] = [];

  // The gaps the employer named. Concrete, and limited to a readable few.
  if (missingSkills.length > 0) {
    const named = missingSkills.slice(0, 3).join(", ");
    suggestions.push({
      id: "close-gaps",
      title: missingSkills.length === 1 ? `Show evidence of ${named}` : `Show evidence of ${named}`,
      body:
        "This job lists it as a requirement and your profile does not evidence it yet. Add a project, a certification, or a bullet that shows where you used it — or say plainly how close you are.",
      action: "profile"
    });
  }

  for (const [index, risk] of risks.slice(0, 2).entries()) {
    suggestions.push({
      id: `risk-${index}`,
      title: "Address this before you apply",
      body: `${risk} Decide how you will answer it, and put the answer in your cover letter rather than leaving it to be guessed.`
    });
  }

  if (match?.recommended_resume_angle) {
    suggestions.push({
      id: "angle",
      title: "Lead with your strongest angle",
      body: match.recommended_resume_angle,
      action: "materials"
    });
  }

  if (matchedSkills.length > 0) {
    suggestions.push({
      id: "foreground-matches",
      title: "Put your matching skills where they are seen first",
      body: `${matchedSkills.slice(0, 4).join(", ")} already line up with this posting. Move them into your summary and your top bullets so a six-second scan finds them.`,
      action: "materials"
    });
  }

  if (job.description_clean && job.description_clean.length > 400) {
    suggestions.push({
      id: "mirror-language",
      title: "Mirror the posting's own wording",
      body: "Read the full description and reuse the employer's phrasing for the responsibilities you have actually done. Screeners and keyword filters both look for it.",
      action: "description"
    });
  }

  return {
    strengths,
    matchedSkills,
    missingSkills,
    risks,
    suggestions,
    hasSignals:
      strengths.length > 0 || matchedSkills.length > 0 || missingSkills.length > 0 || risks.length > 0
  };
}
