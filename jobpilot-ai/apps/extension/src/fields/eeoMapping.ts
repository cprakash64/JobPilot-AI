/**
 * Mapping stored EEO answers onto an ATS control's own option strings.
 *
 * Canonical values (from the API, app/profile/eeo.py) are kept strictly
 * separate from ATS display strings. Every mapping here is conservative and
 * fails CLOSED: when the stored answer cannot be matched to exactly one option
 * whose meaning is the same, the field becomes `needs_confirmation` for the
 * user to answer on the page — never a best guess.
 *
 * Hard rules:
 *   - only fill when the user explicitly stored the answer AND consent is live;
 *   - never infer veteran or disability status from anything;
 *   - never reduce multiple selected races to one; if the control is
 *     single-select and several races are stored, ask;
 *   - a demographic field never triggers auto-submit (enforced by the runner —
 *     these are sensitive fields and are excluded from automatic filling
 *     entirely unless explicitly enabled).
 */

export type EeoField =
  | "gender_identity"
  | "veteran_status"
  | "disability_status"
  | "hispanic_or_latino"
  | "race_ethnicity";

export type EeoMappingOutcome =
  | { status: "fill"; values: string[] }
  | { status: "needs_confirmation"; reason: EeoMappingReason };

export type EeoMappingReason =
  | "NO_STORED_ANSWER"
  | "NO_CONSENT"
  | "NO_MATCHING_OPTION"
  | "AMBIGUOUS_OPTIONS"
  | "MULTI_VALUE_IN_SINGLE_SELECT"
  | "QUESTION_MEANING_UNCLEAR";

/** Normalize an option label for comparison. Never changes meaning. */
export function normalizeOption(text: string): string {
  return text
    .toLowerCase()
    .replace(/[’']/g, "")
    .replace(/[.,;:!?()[\]]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Accepted ATS phrasings per canonical value.
 *
 * Deliberately a CLOSED list. An unrecognised option is not "close enough" —
 * demographic answers are not a place for fuzzy matching.
 */
const OPTION_SYNONYMS: Record<EeoField, Record<string, string[]>> = {
  gender_identity: {
    // "Male"/"Female" are SEX terms. They are accepted only because most ATS
    // gender questions use them as the sole binary options, and mapping is
    // still gated on `allowBinarySexMapping` at the call site.
    woman: ["woman", "female"],
    man: ["man", "male"],
    non_binary: ["non binary", "nonbinary", "non-binary", "gender non conforming", "genderqueer"],
    self_describe: ["self describe", "prefer to self describe", "self-identify", "other"],
    prefer_not_to_answer: [
      "prefer not to answer",
      "prefer not to say",
      "decline to self identify",
      "decline to identify",
      "i dont wish to answer",
      "i do not wish to answer",
      "not specified"
    ]
  },
  veteran_status: {
    protected_veteran: [
      "i identify as one or more of the classifications of a protected veteran",
      "i am a protected veteran",
      "yes i am a protected veteran",
      "protected veteran"
    ],
    not_protected_veteran: [
      "i am not a protected veteran",
      "no i am not a protected veteran",
      "not a protected veteran"
    ],
    not_a_veteran: ["i am not a veteran", "not a veteran", "no i am not a veteran"],
    prefer_not_to_answer: [
      "i dont wish to answer",
      "i do not wish to answer",
      "prefer not to answer",
      "decline to self identify",
      "i dont wish to self identify"
    ]
  },
  disability_status: {
    yes: [
      "yes i have a disability or have had one in the past",
      "yes i have a disability",
      "yes"
    ],
    no: [
      "no i do not have a disability and have not had one in the past",
      "no i dont have a disability",
      "no i do not have a disability",
      "no"
    ],
    prefer_not_to_answer: [
      "i do not want to answer",
      "i dont want to answer",
      "prefer not to answer",
      "decline to self identify"
    ]
  },
  hispanic_or_latino: {
    yes: ["yes", "hispanic or latino", "yes hispanic or latino"],
    no: ["no", "not hispanic or latino", "no not hispanic or latino"],
    prefer_not_to_answer: ["prefer not to answer", "decline to self identify", "i dont wish to answer"]
  },
  race_ethnicity: {
    american_indian_or_alaska_native: [
      "american indian or alaska native",
      "american indian/alaska native",
      "native american or alaska native"
    ],
    asian: ["asian"],
    black_or_african_american: ["black or african american", "black/african american", "black"],
    native_hawaiian_or_other_pacific_islander: [
      "native hawaiian or other pacific islander",
      "native hawaiian/other pacific islander",
      "pacific islander"
    ],
    white: ["white"],
    another_race_or_ethnicity: ["two or more races", "other", "another race or ethnicity"],
    prefer_not_to_answer: ["prefer not to answer", "decline to self identify", "i dont wish to answer"]
  }
};

export interface EeoMappingInput {
  field: EeoField;
  /** Canonical stored value(s). Empty means the user never answered. */
  storedValues: string[];
  /** The option labels this ATS control actually renders. */
  options: string[];
  /** Whether the user's consent to store/use this data is currently active. */
  consentActive: boolean;
  /** Whether the control accepts more than one selection. */
  multiSelect?: boolean;
  /**
   * Set only when the target question is an explicitly binary sex/gender
   * question AND product policy permits mapping gender identity onto it.
   * Defaults to false: "Male"/"Female" are not synonyms for a gender identity.
   */
  allowBinarySexMapping?: boolean;
}

/** Resolve a stored canonical value against this control's options. */
function matchOne(field: EeoField, canonical: string, options: string[], allowBinarySexMapping: boolean): string[] {
  const synonyms = OPTION_SYNONYMS[field]?.[canonical];
  if (!synonyms) return [];

  const binarySexTerms = new Set(["male", "female"]);
  return options.filter((option) => {
    const normalized = normalizeOption(option);
    if (!synonyms.includes(normalized)) return false;
    // A gender IDENTITY answer only maps onto a binary sex term when the caller
    // has established the question really is that binary question.
    if (field === "gender_identity" && binarySexTerms.has(normalized) && !allowBinarySexMapping) {
      return false;
    }
    return true;
  });
}

export function mapEeoAnswer(input: EeoMappingInput): EeoMappingOutcome {
  const { field, storedValues, options, consentActive, multiSelect = false } = input;
  const allowBinarySexMapping = input.allowBinarySexMapping ?? false;

  // Never invent an answer the user did not give.
  const stored = storedValues.filter(Boolean);
  if (stored.length === 0) return { status: "needs_confirmation", reason: "NO_STORED_ANSWER" };
  // Consent governs USE, not just storage.
  if (!consentActive) return { status: "needs_confirmation", reason: "NO_CONSENT" };
  if (options.length === 0) return { status: "needs_confirmation", reason: "NO_MATCHING_OPTION" };

  // Several stored races cannot be honestly expressed in a single-select
  // control; picking one would misrepresent the user.
  if (stored.length > 1 && !multiSelect) {
    return { status: "needs_confirmation", reason: "MULTI_VALUE_IN_SINGLE_SELECT" };
  }

  const chosen: string[] = [];
  for (const canonical of stored) {
    const matches = matchOne(field, canonical, options, allowBinarySexMapping);
    if (matches.length === 0) return { status: "needs_confirmation", reason: "NO_MATCHING_OPTION" };
    // Two options matching the same canonical value means the control's
    // vocabulary is finer-grained than ours; the user must choose.
    if (matches.length > 1) return { status: "needs_confirmation", reason: "AMBIGUOUS_OPTIONS" };
    chosen.push(matches[0]);
  }

  return { status: "fill", values: chosen };
}
