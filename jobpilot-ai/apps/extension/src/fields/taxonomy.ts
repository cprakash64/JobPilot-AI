/**
 * Canonical field taxonomy. Keys align with the backend answer vault
 * (first_name, email, …) so a mapped field maps directly onto a session answer.
 * Upload, custom-question, sensitive, and unknown buckets are added on top.
 */

export type CanonicalField =
  // personal / contact
  | "first_name"
  | "last_name"
  | "full_name"
  | "email"
  | "phone"
  | "address"
  | "city"
  | "state"
  | "postal_code"
  | "country"
  // profile links
  | "linkedin_url"
  | "github_url"
  | "portfolio_url"
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
