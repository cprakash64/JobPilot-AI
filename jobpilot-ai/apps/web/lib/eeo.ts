/**
 * EEO question vocabularies — the client mirror of apps/api/app/profile/eeo.py.
 *
 * The bug this replaces: ONE option list ("Prefer not to answer / Yes / No /
 * Another option") was reused for five questions that mean entirely different
 * things, so the Gender question offered Yes/No and race collapsed to a
 * meaningless "Another option".
 *
 * Each question now has its own closed option set, stable canonical values, and
 * a user-facing label. Nothing is preselected: `null` means "not answered", and
 * that is different from "prefer not to answer" — which is itself an explicit,
 * always-available choice.
 */

export const PREFER_NOT = "prefer_not_to_answer";

export type EeoOption = { value: string; label: string };

export const GENDER_IDENTITY_OPTIONS: EeoOption[] = [
  { value: "woman", label: "Woman" },
  { value: "man", label: "Man" },
  { value: "non_binary", label: "Non-binary" },
  { value: "self_describe", label: "Self-describe" },
  { value: PREFER_NOT, label: "Prefer not to answer" }
];

export const VETERAN_STATUS_OPTIONS: EeoOption[] = [
  { value: "protected_veteran", label: "I am a protected veteran" },
  { value: "not_protected_veteran", label: "I am not a protected veteran" },
  { value: "not_a_veteran", label: "I am not a veteran" },
  { value: PREFER_NOT, label: "Prefer not to answer" }
];

export const DISABILITY_STATUS_OPTIONS: EeoOption[] = [
  { value: "yes", label: "Yes, I have a disability or have had one in the past" },
  { value: "no", label: "No, I do not have a disability and have not had one in the past" },
  { value: PREFER_NOT, label: "Prefer not to answer" }
];

export const HISPANIC_OR_LATINO_OPTIONS: EeoOption[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: PREFER_NOT, label: "Prefer not to answer" }
];

export const RACE_ETHNICITY_OPTIONS: EeoOption[] = [
  { value: "american_indian_or_alaska_native", label: "American Indian or Alaska Native" },
  { value: "asian", label: "Asian" },
  { value: "black_or_african_american", label: "Black or African American" },
  { value: "native_hawaiian_or_other_pacific_islander", label: "Native Hawaiian or Other Pacific Islander" },
  { value: "white", label: "White" },
  { value: "another_race_or_ethnicity", label: "Another race or ethnicity" },
  { value: PREFER_NOT, label: "Prefer not to answer" }
];

export type EeoForm = {
  gender_identity: string | null;
  gender_self_description: string;
  veteran_status: string | null;
  disability_status: string | null;
  hispanic_or_latino: string | null;
  race_ethnicity: string[];
  race_self_description: string;
  consent_to_store: boolean;
};

/** Nothing preselected, consent off. This is the state a NEW user sees. */
export const emptyEeoForm: EeoForm = {
  gender_identity: null,
  gender_self_description: "",
  veteran_status: null,
  disability_status: null,
  hispanic_or_latino: null,
  race_ethnicity: [],
  race_self_description: "",
  consent_to_store: false
};

/**
 * Toggle a race/ethnicity selection.
 *
 * "Prefer not to answer" is mutually exclusive: selecting it clears the rest,
 * and selecting anything else clears it. The two together are contradictory.
 */
export function toggleRaceSelection(current: string[], value: string): string[] {
  if (value === PREFER_NOT) {
    return current.includes(PREFER_NOT) ? [] : [PREFER_NOT];
  }
  const withoutPreferNot = current.filter((item) => item !== PREFER_NOT);
  return withoutPreferNot.includes(value)
    ? withoutPreferNot.filter((item) => item !== value)
    : [...withoutPreferNot, value];
}

/** True when the form carries at least one real answer — which is what makes
 * consent mandatory before saving. */
export function hasAnyAnswer(form: EeoForm): boolean {
  return Boolean(
    form.gender_identity ||
      form.veteran_status ||
      form.disability_status ||
      form.hispanic_or_latino ||
      form.race_ethnicity.length > 0 ||
      form.gender_self_description.trim() ||
      form.race_self_description.trim()
  );
}

/** Normalize an API response into form state (null-safe, like profileForm). */
export function normalizeEeo(record: Partial<EeoForm> | null | undefined): EeoForm {
  if (!record) return { ...emptyEeoForm };
  return {
    gender_identity: record.gender_identity ?? null,
    gender_self_description: record.gender_self_description ?? "",
    veteran_status: record.veteran_status ?? null,
    disability_status: record.disability_status ?? null,
    hispanic_or_latino: record.hispanic_or_latino ?? null,
    race_ethnicity: Array.isArray(record.race_ethnicity) ? record.race_ethnicity : [],
    race_self_description: record.race_self_description ?? "",
    consent_to_store: Boolean(record.consent_to_store)
  };
}
