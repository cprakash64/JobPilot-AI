/**
 * Structured personal names (client mirror of apps/api/app/profile/names.py).
 *
 * Used only to PRE-FILL the resume-import review with a proposed split so the
 * user can correct it before saving. It is never the thing that gets persisted
 * without review — that is the whole point. "Chandra Prakash Pandey" is
 * proposed as Chandra / Prakash / Pandey, but `certain` is false, so the review
 * UI asks rather than assumes.
 */

const SURNAME_PARTICLES = new Set([
  "van", "von", "de", "del", "della", "der", "den", "di", "da", "dos",
  "das", "du", "la", "le", "el", "al", "bin", "ibn", "bint", "ap",
  "mac", "mc", "st", "san", "santa", "ter", "ten", "op"
]);

const PREFIXES = new Set(["mr", "mrs", "ms", "mx", "dr", "prof", "sir", "madam", "miss"]);
const SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "mba", "esq", "dds", "rn"]);

export interface NameSuggestion {
  firstName: string;
  middleName: string;
  lastName: string;
  suffix: string;
  /** True only for an unambiguous split; multi-token names are always false. */
  certain: boolean;
}

const EMPTY: NameSuggestion = { firstName: "", middleName: "", lastName: "", suffix: "", certain: false };

const key = (token: string) => token.toLowerCase().replace(/^[.,]+|[.,]+$/g, "");

/** Title-case a name that arrived SHOUTING (a resume-header typography
 * artifact). Anything containing a lowercase letter is left untouched. */
export function normalizeDisplayCase(name: string): string {
  const text = (name || "").trim();
  if (!text || /[a-z]/.test(text)) return text;
  return text
    .split(/\s+/)
    .map((token) => {
      if (SURNAME_PARTICLES.has(key(token))) return token.toLowerCase();
      let out = token.charAt(0).toUpperCase() + token.slice(1).toLowerCase();
      for (const sep of ["-", "'", "’"]) {
        if (out.includes(sep)) {
          out = out
            .split(sep)
            .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
            .join(sep);
        }
      }
      return out;
    })
    .join(" ");
}

export function suggestNameParts(fullName: string): NameSuggestion {
  const raw = normalizeDisplayCase(fullName);
  if (!raw) return EMPTY;

  // "Pandey, Chandra Prakash" states the family name explicitly.
  if (raw.includes(",")) {
    const [familyPart, ...rest] = raw.split(",");
    const family = familyPart.split(/\s+/).filter((t) => t && !SUFFIXES.has(key(t))).join(" ");
    const given = rest.join(",").split(/\s+/).filter((t) => t && !PREFIXES.has(key(t)));
    if (family && given.length > 0) {
      return {
        firstName: given[0],
        middleName: given.slice(1).join(" "),
        lastName: family,
        suffix: "",
        certain: true
      };
    }
  }

  let tokens = raw.split(/\s+/).filter(Boolean);
  while (tokens.length > 0 && PREFIXES.has(key(tokens[0]))) tokens = tokens.slice(1);

  const suffixParts: string[] = [];
  while (tokens.length > 1 && SUFFIXES.has(key(tokens[tokens.length - 1]))) {
    suffixParts.unshift(tokens[tokens.length - 1]);
    tokens = tokens.slice(0, -1);
  }
  const suffix = suffixParts.join(" ");

  if (tokens.length === 0) return { ...EMPTY, suffix };
  if (tokens.length === 1) {
    return { firstName: tokens[0], middleName: "", lastName: "", suffix, certain: true };
  }

  const particleAt = tokens.findIndex((t, i) => i > 0 && SURNAME_PARTICLES.has(key(t)));
  if (particleAt > 0) {
    return {
      firstName: tokens[0],
      middleName: tokens.slice(1, particleAt).join(" "),
      lastName: tokens.slice(particleAt).join(" "),
      suffix,
      certain: false
    };
  }

  if (tokens.length === 2) {
    return { firstName: tokens[0], middleName: "", lastName: tokens[1], suffix, certain: true };
  }

  // Three or more tokens: propose first / middle… / last, but never as certain —
  // this is exactly the shape that produced last_name = "Prakash Pandey".
  return {
    firstName: tokens[0],
    middleName: tokens.slice(1, -1).join(" "),
    lastName: tokens[tokens.length - 1],
    suffix,
    certain: false
  };
}

export function composeFullName(parts: {
  firstName?: string | null;
  middleName?: string | null;
  lastName?: string | null;
}): string {
  // Tolerates null/undefined parts deliberately. The real guard is
  // lib/profileForm.ts normalizing the API's nulls at one boundary, but this
  // function is called directly from render paths, and a `null.trim()` here
  // takes down the whole page through the error boundary — which is exactly
  // what happened when migration 0013 started returning null name parts.
  return [parts.firstName, parts.middleName, parts.lastName]
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(" ");
}
