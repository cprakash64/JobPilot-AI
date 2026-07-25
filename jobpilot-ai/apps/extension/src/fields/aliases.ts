/**
 * Controlled alias table for dropdown/option-text matching. Used ONLY at
 * fill time to match a verified answer value against the ATS's actual
 * rendered option labels — never at classification time, and never a broad
 * fuzzy-similarity match. Each group is a closed set of known-equivalent
 * strings; a target matches an option only if both normalize into the same
 * group (or are identical after normalization).
 */

const ALIAS_GROUPS: string[][] = [
  ["yes", "yes i will require sponsorship", "yes i will need sponsorship", "i will require sponsorship"],
  ["no", "no i will not require sponsorship", "no i will not need sponsorship", "i will not require sponsorship"],
  ["united states", "united states of america", "usa", "us", "u.s.", "u.s.a."],
  ["united kingdom", "uk", "u.k.", "great britain"],
  ["man", "male"],
  ["woman", "female"],
  ["self describe", "self-describe", "prefer to self describe", "other"],
  ["american indian or alaska native", "american indian or alaskan native"],
  ["bachelors degree", "bachelor degree", "bachelor of science", "bachelor of arts", "bs", "ba", "bsc"],
  ["masters degree", "master degree", "master of science", "master of arts", "ms", "ma", "msc"],
  ["doctoral degree", "doctorate", "doctor of philosophy", "phd"],
  ["associates degree", "associate degree", "associate of science", "associate of arts", "as", "aa"],
  ["high school diploma", "high school", "secondary school"],
  // US state name <-> postal abbreviation.
  ["alabama", "al"], ["alaska", "ak"], ["arizona", "az"], ["arkansas", "ar"],
  ["california", "ca"], ["colorado", "co"], ["connecticut", "ct"], ["delaware", "de"],
  ["florida", "fl"], ["georgia", "ga"], ["hawaii", "hi"], ["idaho", "id"],
  ["illinois", "il"], ["indiana", "in"], ["iowa", "ia"], ["kansas", "ks"],
  ["kentucky", "ky"], ["louisiana", "la"], ["maine", "me"], ["maryland", "md"],
  ["massachusetts", "ma"], ["michigan", "mi"], ["minnesota", "mn"], ["mississippi", "ms"],
  ["missouri", "mo"], ["montana", "mt"], ["nebraska", "ne"], ["nevada", "nv"],
  ["new hampshire", "nh"], ["new jersey", "nj"], ["new mexico", "nm"], ["new york", "ny"],
  ["north carolina", "nc"], ["north dakota", "nd"], ["ohio", "oh"], ["oklahoma", "ok"],
  ["oregon", "or"], ["pennsylvania", "pa"], ["rhode island", "ri"], ["south carolina", "sc"],
  ["south dakota", "sd"], ["tennessee", "tn"], ["texas", "tx"], ["utah", "ut"],
  ["vermont", "vt"], ["virginia", "va"], ["washington", "wa"], ["west virginia", "wv"],
  ["wisconsin", "wi"], ["wyoming", "wy"],
  ["prefer not to answer", "prefer not to say", "decline to self-identify", "decline to self identify", "decline to answer", "i do not wish to answer"]
];

const GROUP_INDEX = new Map<string, number>();
ALIAS_GROUPS.forEach((group, i) => {
  for (const term of group) GROUP_INDEX.set(term, i);
});

/** Lowercase, strip punctuation, collapse whitespace — never changes meaning,
 * only normalizes for comparison. */
export function normalizeForMatch(text: string): string {
  return text
    .toLowerCase()
    .replace(/[.,;:!?'"()]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Match an international dialing code against a country-selector option.
 *
 * Phone-country selectors render the same choice many ways — "United States
 * (+1)", "US +1", "+1", "🇺🇸 +1" — so exact label matching fails on nearly all
 * of them. This stays precise rather than fuzzy: it only engages when the
 * ANSWER is literally a dial code ("+1"), and it compares the option's own
 * "+<digits>" token numerically. An option with no dial code in its label never
 * matches, so an unmatched control becomes a question instead of a wrong guess.
 */
export function dialCodeMatches(optionLabel: string, value: string): boolean {
  const wanted = /^\+\s?(\d{1,4})$/.exec(value.trim());
  if (!wanted) return false;
  const found = optionLabel.match(/\+\s?\d{1,4}/g);
  if (!found) return false;
  return found.some((token) => token.replace(/\D/g, "") === wanted[1]);
}

/** Match the structured country+calling-code value used by split phone fields.
 *
 * A country selector may render the same choice as "United States (+1)",
 * "US +1", or with a flag prefix. Requiring both the controlled country alias
 * and the exact calling-code token avoids the ambiguity of matching bare +1
 * (which is shared by multiple North American countries).
 */
export function phoneCountryOptionMatches(optionLabel: string, value: string): boolean {
  const wanted = /^(.*?)\s*\(\s*(\+\s?\d{1,4})\s*\)\s*$/.exec(value.trim());
  if (!wanted) return false;
  const wantedCountry = wanted[1].trim();
  const wantedCode = wanted[2].replace(/\s+/g, "");
  const code = optionLabel.match(/\+\s?\d{1,4}/)?.[0]?.replace(/\s+/g, "");
  if (!code || code !== wantedCode) return false;
  const withoutFlagOrCode = optionLabel
    .replace(/\+\s?\d{1,4}/g, "")
    .replace(/[()]/g, " ")
    // Emoji flags are pairs of regional-indicator symbols. Removing all
    // non-word symbols here affects comparison only, never the value written.
    .replace(/[^\p{L}\p{N}.\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
  return aliasMatches(withoutFlagOrCode, wantedCountry);
}

/** Match a saved graduation year to a range offered by the employer.
 * Example: 2025 safely selects "2023–2026" because the endpoints are explicit
 * and inclusive. It also handles "Before 2020" without guessing. */
export function graduationYearOptionMatches(optionLabel: string, value: string): boolean {
  const year = Number(value.trim());
  if (!/^\d{4}$/.test(value.trim()) || year < 1900 || year > 2200) return false;
  const label = optionLabel.replace(/[–—]/g, "-").trim();
  const range = /(?:^|\D)(\d{4})\s*-\s*(\d{4})(?:\D|$)/.exec(label);
  if (range) return year >= Number(range[1]) && year <= Number(range[2]);
  const before = /\bbefore\s+(\d{4})\b/i.exec(label);
  if (before) return year < Number(before[1]);
  const after = /\b(?:after|from)\s+(\d{4})\b/i.exec(label);
  if (after) return year >= Number(after[1]);
  return false;
}

/** Match GPA dropdown ranges, or choose the employer's closest numeric
 * conversion when every offered option is a numeric GPA value. */
export function gpaOptionMatches(optionLabel: string, value: string): boolean {
  const gpa = Number(value.trim());
  if (!Number.isFinite(gpa) || gpa < 0 || gpa > 4) return false;
  const label = optionLabel.replace(/[–—]/g, "-").trim();
  const range = /(\d(?:\.\d+)?)\s*-\s*(\d(?:\.\d+)?)/.exec(label);
  return Boolean(range && gpa >= Number(range[1]) && gpa <= Number(range[2]));
}

export function closestGpaOption<T extends { label: string }>(options: T[], value: string): T | null {
  const gpa = Number(value.trim());
  if (!Number.isFinite(gpa) || gpa < 0 || gpa > 4) return null;
  const candidates = options
    .map((option) => {
      const label = option.label.trim();
      const exact = /^(\d(?:\.\d+)?)$/.exec(label);
      return exact ? { option, numeric: Number(exact[1]) } : null;
    })
    .filter((item): item is { option: T; numeric: number } => Boolean(item && item.numeric >= 0 && item.numeric <= 4));
  if (candidates.length < 2) return null;
  candidates.sort((a, b) => Math.abs(a.numeric - gpa) - Math.abs(b.numeric - gpa));
  return candidates[0].option;
}

/** Match a structured location against an autocomplete result without fuzzy
 * city guessing. At least city + one disambiguator are required, and every
 * comma-delimited component must be exactly equal or a controlled alias
 * ("AZ" <-> "Arizona"). */
export function locationOptionMatches(optionLabel: string, value: string): boolean {
  const wanted = value.split(",").map((part) => part.trim()).filter(Boolean);
  const option = optionLabel.split(",").map((part) => part.trim()).filter(Boolean);
  if (wanted.length < 2 || option.length < 2 || option.length > wanted.length) return false;
  // The ATS may display "Tempe, AZ" while the profile supplies
  // "Tempe, Arizona, United States". Require exact city plus every
  // disambiguator the option actually shows; never accept a city-only guess.
  return option.every((part, index) => aliasMatches(part, wanted[index]));
}

/** True if two option/answer strings should be treated as the same choice:
 * identical after normalization, or in the same controlled alias group.
 * Never a broad fuzzy/similarity match. */
export function aliasMatches(a: string, b: string): boolean {
  const na = normalizeForMatch(a);
  const nb = normalizeForMatch(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  const ga = GROUP_INDEX.get(na);
  const gb = GROUP_INDEX.get(nb);
  return ga != null && ga === gb;
}

/** Match a verified binary answer to an employer's verbose Yes/No option.
 *
 * Greenhouse commonly renders values such as "Yes, I am currently legally
 * authorized…" and "No, I do not and will not require sponsorship…", while
 * the profile stores `authorized_us` or a literal Yes/No. This is deliberately
 * narrower than fuzzy text matching: the answer must be an explicit known
 * binary value and the option itself must START with Yes or No. */
export function binaryAnswerMatches(optionLabel: string, answer: string): boolean {
  const target = normalizeForMatch(answer).replace(/\s+/g, "_");
  const wanted = new Map<string, boolean>([
    ["yes", true], ["true", true], ["1", true],
    ["authorized_us", true], ["authorized_to_work_in_the_united_states", true],
    ["no", false], ["false", false], ["0", false],
    ["not_authorized", false], ["not_currently_authorized", false]
  ]).get(target);
  if (wanted == null) return false;
  const option = normalizeForMatch(optionLabel);
  if (/^yes\b/.test(option)) return wanted;
  if (/^no\b/.test(option)) return !wanted;
  if (/^(?:i am |currently )?(?:legally )?authorized to work\b/.test(option)) return wanted;
  if (/^(?:i am )?not (?:currently )?(?:legally )?authorized to work\b/.test(option)) return !wanted;
  return false;
}

/** Match the user-approved referral default to the employer's own careers
 * source. The sentinel cannot match LinkedIn, search engines, agencies, or an
 * employee referral, and there is deliberately no first-option fallback. */
export function companyCareersSourceMatches(optionLabel: string, answer: string): boolean {
  if (answer !== "__jobpilot_company_careers_page__") return false;
  const option = normalizeForMatch(optionLabel);
  if (/\b(?:employee|personal) referral\b/.test(option)) return false;
  return (
    /\bcompany\b.*\b(?:website|careers?|career site|career page)\b/.test(option) ||
    /\b(?:website|careers?|career site|career page)\b.*\bcompany\b/.test(option) ||
    /^(?:employer|company) careers? (?:page|site|website)$/.test(option) ||
    /^careers? (?:page|site|website)$/.test(option)
  );
}

/** A privacy acknowledgement may be selected only when the employer presents
 * one substantive choice. This prevents the sentinel from affirming a factual
 * or legal multi-choice question, even if one option happens to say “Yes”. */
export function singletonPrivacyAcknowledgementMatches(
  optionLabel: string,
  answer: string,
  substantiveOptionLabels: string[]
): boolean {
  if (answer !== "__jobpilot_privacy_acknowledgement__" || substantiveOptionLabels.length !== 1) return false;
  const option = normalizeForMatch(optionLabel);
  return /^(?:i\s+)?(?:agree|acknowledge|consent)\b/.test(option) || /^yes\b/.test(option);
}

/** User-approved rule for mandatory choice controls: select an affirmative
 * option only when it is the employer's ONE substantive choice. A Yes/No or
 * agree/decline question therefore never passes this matcher. */
export function singletonRequiredAffirmationMatches(
  optionLabel: string,
  answer: string,
  substantiveOptionLabels: string[]
): boolean {
  if (answer !== "__jobpilot_required_singleton_affirmation__" || substantiveOptionLabels.length !== 1) return false;
  const option = normalizeForMatch(optionLabel);
  return /^(?:i\s+)?(?:agree|acknowledge|consent|accept)\b/.test(option) || /^yes\b/.test(option);
}
