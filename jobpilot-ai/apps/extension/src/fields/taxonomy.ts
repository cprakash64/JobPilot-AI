/**
 * Canonical field taxonomy. Keys align with the backend answer vault
 * (first_name, email, …) so a mapped field maps directly onto a session answer.
 * Upload, custom-question, sensitive, and unknown buckets are added on top.
 */

export type CanonicalField =
  // personal / contact
  | "first_name"
  | "middle_name"
  | "last_name"
  | "full_name"
  | "preferred_name"
  | "preferred_first_name"
  | "preferred_last_name"
  | "pronouns"
  | "email"
  // Phone comes in three shapes so a form that splits the country selector from
  // the national number can be filled without concatenating (and without
  // duplicating "+1"). "phone" is E.164 and is used for single-input forms.
  | "phone"
  | "phone_country"
  | "phone_national"
  | "address"
  | "city"
  | "state"
  | "postal_code"
  | "country"
  // profile links
  | "linkedin_url"
  | "github_url"
  | "portfolio_url"
  // company-specific (never reused across employers — see mapping.ts / vault scope)
  | "referral_source"
  | "previously_employed"
  | "relatives_employed"
  | "previously_interviewed"
  // employment / education
  | "current_company"
  | "current_title"
  | "employment_history"
  | "education"
  | "skills"
  // work authorization (consequential)
  | "work_authorization_us"
  | "sponsorship_required_now"
  | "sponsorship_required_future"
  // A narrow, non-factual consent: the employer's candidate/data privacy
  // policy acknowledgement. This is intentionally separate from
  // `legal_attestation`, which may assert facts and always needs an explicit
  // stored answer.
  | "privacy_policy_acknowledgement"
  // location / preferences
  | "willing_to_relocate"
  | "preferred_workplace"
  | "salary_expectation"
  | "available_start_date"
  | "years_of_experience"
  // documents
  | "resume_upload"
  | "cover_letter_upload"
  // generated written responses
  | "custom_motivation"
  | "custom_experience"
  // sensitive / consequential (never auto-filled unless explicitly verified)
  | "gender"
  | "race"
  | "ethnicity"
  | "disability_status"
  | "veteran_status"
  | "sexual_orientation"
  | "religion"
  | "criminal_history"
  | "legal_attestation"
  | "security_clearance"
  | "export_control"
  | "salary_history"
  | "government_demographic"
  | "voluntary_eeo"
  | "unknown";

export type MappingSource =
  | "autocomplete"
  | "ats_rule"
  | "deterministic"
  | "label"
  | "saved_mapping"
  | "semantic"
  | "llm"
  | "user";

/** Written-response fields the copilot may draft (always AI-marked + reviewed). */
export const CUSTOM_RESPONSE_FIELDS: ReadonlySet<CanonicalField> = new Set([
  "custom_motivation",
  "custom_experience"
]);

export const UPLOAD_FIELDS: ReadonlySet<CanonicalField> = new Set(["resume_upload", "cover_letter_upload"]);

/** Canonical keys whose saved answers default to "company" scope in the
 * answer vault — reusable at THIS employer only, never auto-applied to a
 * different one (mirrors apps/api's COMPANY_SCOPED_KEYS). */
export const COMPANY_SCOPED_FIELDS: ReadonlySet<CanonicalField> = new Set([
  "referral_source",
  "previously_employed",
  "relatives_employed",
  "previously_interviewed"
]);

export function defaultScopeForField(key: CanonicalField): "global" | "company" {
  return COMPANY_SCOPED_FIELDS.has(key) ? "company" : "global";
}
