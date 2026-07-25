/**
 * THE single boundary between the profile API wire format and the form state.
 *
 * The crash this exists to prevent: the profile page had TWO conversions — a
 * hardened `formFromProfile()` used only on the import path, and an inline
 * `{ ...emptyProfile, ...profile }` spread in the load effect that coalesced
 * only the arrays. Migration 0013 then started returning `null` for
 * first_name/middle_name/last_name, the raw spread overwrote the empty-string
 * defaults with those nulls, and rendering Basic Info called `null.trim()`.
 *
 * Rules enforced here, once, for every caller:
 *   - the wire may send null/undefined for anything;
 *   - controlled text inputs receive "" (never null/undefined, so an input
 *     never flips between uncontrolled and controlled);
 *   - list fields receive [];
 *   - booleans are explicit;
 *   - country/phone tolerate incomplete legacy rows;
 *   - full_name is DERIVED from the parts, never an independent source of
 *     truth that can disagree with them.
 *
 * There must be exactly one of these functions. If you find yourself spreading
 * an API profile into form state anywhere else, call this instead.
 */

import { composeFullName } from "@/lib/names";

export type RemotePreference = "everything" | "remote" | "hybrid" | "onsite";

export type ProfileForm = {
  // Structured identity — the source of truth. `full_name` is derived.
  first_name: string;
  middle_name: string;
  last_name: string;
  preferred_first_name: string;
  preferred_last_name: string;
  full_name: string;
  name_confirmed: boolean;
  // Contact
  /** Account/login email — read-only here, changed in Settings. */
  email: string;
  /** The address used ON APPLICATIONS. Editable, separate from login. */
  application_email: string;
  application_email_confirmed: boolean;
  /** Write-only. It is sent only to the dedicated encrypted-credentials route. */
  workday_password: string;
  workday_password_configured: boolean;
  phone: string;
  phone_country_iso2: string;
  location_city: string;
  location_state: string;
  location_postal_code: string;
  location_country: string;
  // Links
  linkedin_url: string;
  github_url: string;
  portfolio_url: string;
  // Work eligibility
  work_authorization: string;
  requires_sponsorship: boolean;
  open_to_relocation: boolean;
  // Targets
  target_roles: string[];
  target_levels: string[];
  preferred_locations: string[];
  remote_preference: RemotePreference;
  skills: string[];
};

/** Wire shape: every field optional, and every value possibly null. */
export type ProfileWire = Partial<Record<keyof ProfileForm, unknown>> & {
  work_authorization_status?: unknown;
  work_preference?: unknown;
  // Pre-0013 clients/rows.
  given_name?: unknown;
  family_name?: unknown;
};

export const emptyProfile: ProfileForm = {
  first_name: "",
  middle_name: "",
  last_name: "",
  preferred_first_name: "",
  preferred_last_name: "",
  full_name: "",
  name_confirmed: false,
  email: "",
  application_email: "",
  application_email_confirmed: false,
  workday_password: "",
  workday_password_configured: false,
  phone: "",
  phone_country_iso2: "US",
  location_city: "",
  location_state: "",
  location_postal_code: "",
  location_country: "United States",
  linkedin_url: "",
  github_url: "",
  portfolio_url: "",
  work_authorization: "prefer_not_to_say",
  requires_sponsorship: false,
  open_to_relocation: false,
  target_roles: [],
  target_levels: [],
  preferred_locations: [],
  remote_preference: "everything",
  skills: []
};

const REMOTE_PREFERENCES: RemotePreference[] = ["everything", "remote", "hybrid", "onsite"];

/** null | undefined | number | anything -> a string safe for a controlled input. */
export function str(value: unknown, fallback = ""): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function strList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim() !== "");
}

function remotePreference(...candidates: unknown[]): RemotePreference {
  for (const candidate of candidates) {
    if (typeof candidate === "string" && (REMOTE_PREFERENCES as string[]).includes(candidate)) {
      return candidate as RemotePreference;
    }
  }
  return "everything";
}

/**
 * Normalize an API profile into form state.
 *
 * `email` is not part of the profile record (it belongs to the account), so it
 * is passed in separately by the caller rather than invented here.
 */
export function normalizeProfile(profile: ProfileWire | null | undefined, email = ""): ProfileForm {
  if (!profile) {
    return { ...emptyProfile, email: str(email) };
  }

  // Legacy rows may carry only given_name/family_name. Accepted as aliases —
  // but nothing is SPLIT here: a legacy full_name-only profile leaves the parts
  // blank so the user confirms the split themselves.
  const first = str(profile.first_name) || str(profile.given_name);
  const middle = str(profile.middle_name);
  const last = str(profile.last_name) || str(profile.family_name);

  return {
    first_name: first,
    middle_name: middle,
    last_name: last,
    preferred_first_name: str(profile.preferred_first_name),
    preferred_last_name: str(profile.preferred_last_name),
    // Derived when the parts exist; otherwise fall back to the stored display
    // value so a legacy profile still shows the name it has.
    full_name: composeFullName({ firstName: first, middleName: middle, lastName: last }) || str(profile.full_name),
    name_confirmed: Boolean(profile.name_confirmed),

    email: str(email),
    application_email: str(profile.application_email),
    application_email_confirmed: Boolean(profile.application_email_confirmed),
    // The API intentionally never returns the secret itself.
    workday_password: "",
    workday_password_configured: Boolean(profile.workday_password_configured),
    phone: str(profile.phone),
    // Incomplete legacy phone rows have no ISO2; default to US rather than
    // rendering a select with a value none of its options match.
    phone_country_iso2: str(profile.phone_country_iso2) || "US",
    location_city: str(profile.location_city),
    location_state: str(profile.location_state),
    location_postal_code: str(profile.location_postal_code),
    location_country: str(profile.location_country) || "United States",

    linkedin_url: str(profile.linkedin_url),
    github_url: str(profile.github_url),
    portfolio_url: str(profile.portfolio_url),

    work_authorization: str(profile.work_authorization_status) || str(profile.work_authorization) || "prefer_not_to_say",
    requires_sponsorship: Boolean(profile.requires_sponsorship),
    open_to_relocation: Boolean(profile.open_to_relocation),

    target_roles: strList(profile.target_roles),
    target_levels: strList(profile.target_levels),
    preferred_locations: strList(profile.preferred_locations),
    remote_preference: remotePreference(profile.work_preference, profile.remote_preference),
    skills: strList(profile.skills)
  };
}

/** The payload PUT /profile expects. `full_name` is sent as the derived value
 * so the stored display name can never drift from the parts. */
export function profileToWire(form: ProfileForm): Record<string, unknown> {
  return {
    full_name: composeFullName({
      firstName: form.first_name,
      middleName: form.middle_name,
      lastName: form.last_name
    }),
    application_email: form.application_email || null,
    phone: form.phone,
    location_city: form.location_city,
    location_state: form.location_state,
    location_postal_code: form.location_postal_code,
    location_country: form.location_country,
    linkedin_url: form.linkedin_url || null,
    github_url: form.github_url || null,
    portfolio_url: form.portfolio_url || null,
    work_authorization: form.work_authorization,
    work_authorization_status: form.work_authorization,
    requires_sponsorship: form.requires_sponsorship,
    open_to_relocation: form.open_to_relocation,
    target_roles: form.target_roles,
    target_levels: form.target_levels,
    preferred_locations: form.preferred_locations,
    remote_preference: form.remote_preference,
    work_preference: form.remote_preference,
    skills: form.skills
  };
}
