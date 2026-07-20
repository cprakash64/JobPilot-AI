/**
 * Fit-score visual tone.
 *
 * Maps a 0–100 fit score to a color theme + label so the score card instantly
 * communicates likelihood:
 *
 *   >= 80  green        "Strong fit"
 *   60–79  lime         "Good fit"    (yellow-green — decent, not as strong as green)
 *   45–59  orange       "Stretch"     (warning — noticeably weaker than 60–79)
 *   < 45   red          "Low fit"
 *
 * Dark mode keeps the SAME HUE per band — the colour carries the score's
 * meaning and must not change between themes. Only lightness is adjusted, so
 * the label stays readable on a dark surface (WCAG AA).
 *
 * IMPORTANT: the class strings below are STATIC and use standard Tailwind
 * palette utilities. They must never be built dynamically (e.g. `bg-${c}-50`)
 * or Tailwind's compiler cannot see them and the colors get purged. This file
 * is included in tailwind.config `content` so these literals are compiled.
 */

export type FitToneKey = "strong" | "good" | "stretch" | "low" | "none";

export type FitScoreTone = {
  key: FitToneKey;
  label: "Strong fit" | "Good fit" | "Stretch" | "Low fit" | "Not scored";
  /** Container classes: border + background tint + text color for the card. */
  container: string;
  /** Class for the large score number. */
  number: string;
  description: string;
};

export const FIT_SCORE_STYLES: Record<FitToneKey, Omit<FitScoreTone, "key">> = {
  strong: {
    label: "Strong fit",
    container:
      "border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-50",
    number: "text-emerald-700 dark:text-emerald-300",
    description: "Your profile strongly matches this role."
  },
  good: {
    label: "Good fit",
    container:
      "border-lime-300 bg-lime-50 text-lime-950 dark:border-lime-700 dark:bg-lime-950 dark:text-lime-50",
    number: "text-lime-700 dark:text-lime-300",
    description: "A decent chance with a few gaps to address."
  },
  stretch: {
    label: "Stretch",
    container:
      "border-orange-300 bg-orange-50 text-orange-950 dark:border-orange-700 dark:bg-orange-950 dark:text-orange-50",
    number: "text-orange-700 dark:text-orange-300",
    description: "A stretch — expect notable skill gaps."
  },
  low: {
    label: "Low fit",
    container:
      "border-red-300 bg-red-50 text-red-950 dark:border-red-700 dark:bg-red-950 dark:text-red-50",
    number: "text-red-700 dark:text-red-300",
    description: "This role is far from your current profile."
  },
  none: {
    label: "Not scored",
    container: "border-line bg-panel text-[var(--text-muted)]",
    number: "text-[var(--text-muted)]",
    description: "Refresh matches after completing your profile for a fit score."
  }
};

export function getFitScoreTone(score: number | null | undefined): FitScoreTone {
  const key = fitToneKey(score);
  return { key, ...FIT_SCORE_STYLES[key] };
}

export type ScoreStateValue =
  | "pending"
  | "scoring"
  | "scored"
  | "failed"
  | "profile_incomplete";

export type ScoreDisplay = {
  /** How to render the card body. */
  kind: "score" | "calculating" | "profile_incomplete" | "failed";
  label: string;
  helper: string;
  /** Whether the list should keep polling because this score is still settling. */
  pending: boolean;
};

/**
 * Resolve what to show for a match given its lifecycle state.
 *
 * A missing state with no score is treated as a transient "calculating" rather
 * than a permanent "Not scored": a background task is expected to fill it in, so
 * the list should poll instead of showing a dead end.
 */
export function getScoreDisplay(
  state: ScoreStateValue | null | undefined,
  score: number | null | undefined
): ScoreDisplay {
  const resolved = state ?? (typeof score === "number" ? "scored" : "pending");
  switch (resolved) {
    case "scored":
      return { kind: "score", label: "", helper: "", pending: false };
    case "profile_incomplete":
      return {
        kind: "profile_incomplete",
        label: "Complete your profile for a fit score",
        helper: "We need your target roles or skills to score this role.",
        pending: false
      };
    case "failed":
      return {
        kind: "failed",
        label: "Score unavailable",
        helper: "We couldn't score this role. Retry, or it will be retried automatically.",
        pending: false
      };
    case "pending":
    case "scoring":
    default:
      return {
        kind: "calculating",
        label: "Calculating fit…",
        helper: "Scoring this role against your profile.",
        pending: true
      };
  }
}

function fitToneKey(score: number | null | undefined): FitToneKey {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return "none";
  }
  if (score >= 80) return "strong";
  if (score >= 60) return "good";
  if (score >= 45) return "stretch";
  return "low";
}
