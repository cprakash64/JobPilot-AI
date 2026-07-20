/**
 * Sensitive / consequential field detection.
 *
 * These are NEVER auto-filled, guessed, inferred, or sent to an LLM. When a
 * field's text matches one of these categories, the copilot marks it "review
 * required" and leaves it for the user to answer directly on the employer page.
 */

import type { CanonicalField } from "./taxonomy";

type SensitiveRule = { key: CanonicalField; patterns: RegExp[] };

const RULES: SensitiveRule[] = [
  { key: "gender", patterns: [/\bgender\b/i, /\bsex\b(?!ual)/i] },
  { key: "sexual_orientation", patterns: [/sexual orientation/i] },
  { key: "race", patterns: [/\brace\b/i] },
  { key: "ethnicity", patterns: [/ethnic/i, /hispanic|latino/i] },
  { key: "disability_status", patterns: [/disabilit/i] },
  { key: "veteran_status", patterns: [/veteran/i, /protected veteran/i] },
  { key: "religion", patterns: [/religio/i] },
  { key: "criminal_history", patterns: [/criminal/i, /felony|conviction|convicted/i, /background check consent/i] },
  { key: "security_clearance", patterns: [/security clearance/i, /clearance level/i] },
  { key: "export_control", patterns: [/export control/i, /\bitar\b|\bear\b/i] },
  { key: "salary_history", patterns: [/salary history/i, /current salary/i, /previous (?:salary|compensation)/i] },
  {
    key: "legal_attestation",
    patterns: [/i (?:certify|attest|acknowledge)/i, /\battestation\b/i, /under penalty of perjury/i, /electronic signature/i]
  },
  {
    key: "government_demographic",
    patterns: [/voluntary self-?identification/i, /eeo/i, /equal employment opportunity/i, /affirmative action/i]
  }
];

export type SensitiveMatch = { sensitive: boolean; key?: CanonicalField; category?: string };

/** Classify free text (label + nearby text + heading) against sensitive rules. */
export function detectSensitive(text: string): SensitiveMatch {
  const haystack = (text || "").toLowerCase();
  if (!haystack.trim()) {
    return { sensitive: false };
  }
  for (const rule of RULES) {
    if (rule.patterns.some((pattern) => pattern.test(haystack))) {
      return { sensitive: true, key: rule.key, category: rule.key };
    }
  }
  return { sensitive: false };
}

const SENSITIVE_KEYS: ReadonlySet<CanonicalField> = new Set(RULES.map((r) => r.key).concat("voluntary_eeo"));

export function isSensitiveKey(key: CanonicalField): boolean {
  return SENSITIVE_KEYS.has(key);
}
