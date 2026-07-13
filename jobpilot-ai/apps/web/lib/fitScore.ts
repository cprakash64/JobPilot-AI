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
    container: "border-emerald-300 bg-emerald-50 text-emerald-950",
    number: "text-emerald-700",
    description: "Your profile strongly matches this role."
  },
  good: {
    label: "Good fit",
    container: "border-lime-300 bg-lime-50 text-lime-950",
    number: "text-lime-700",
    description: "A decent chance with a few gaps to address."
  },
  stretch: {
    label: "Stretch",
    container: "border-orange-300 bg-orange-50 text-orange-950",
    number: "text-orange-700",
    description: "A stretch — expect notable skill gaps."
  },
  low: {
    label: "Low fit",
    container: "border-red-300 bg-red-50 text-red-950",
    number: "text-red-700",
    description: "This role is far from your current profile."
  },
  none: {
    label: "Not scored",
    container: "border-line bg-panel text-[#5d675f]",
    number: "text-[#5d675f]",
    description: "Refresh matches after completing your profile for a fit score."
  }
};

export function getFitScoreTone(score: number | null | undefined): FitScoreTone {
  const key = fitToneKey(score);
  return { key, ...FIT_SCORE_STYLES[key] };
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
