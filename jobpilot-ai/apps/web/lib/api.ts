export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ResumeContent = {
  header: { full_name: string; email: string; phone: string; location: string; links: string[] };
  summary: string;
  skills: { category: string; items: string[] }[];
  experience: { title: string; company: string; location: string; dates: string; bullets: string[] }[];
  projects: { name: string; technologies: string[]; bullets: string[] }[];
  education: { school: string; degree: string; dates: string; details: string }[];
  awards: { name: string; issuer?: string; date?: string }[];
  certifications?: { name: string }[];
  missing_skills?: string[];
};

export type CoverLetterContent = {
  date: string;
  recipient: string;
  company: string;
  role: string;
  greeting: string;
  paragraphs: string[];
  closing: string;
  signature: string;
};

export type DocumentQuality = {
  ats_friendly?: boolean;
  job_tailored?: boolean;
  unsupported_claims_removed?: string[];
  missing_job_skills_not_claimed?: string[];
  estimated_page_count?: number;
  word_count?: number;
  warnings?: string[];
};

export type GeneratedDocument = {
  document_id: number;
  document_type: "resume" | "cover_letter";
  title: string;
  content: Record<string, unknown>;
  markdown: string;
  plain_text: string;
  quality: DocumentQuality;
  model_used?: string | null;
  warnings: string[];
  unsupported_claims_removed: string[];
  download_urls: { docx: string | null; pdf: string | null };
};

export type RefreshSummary = {
  matched_count: number;
  rescored_count: number;
  raw_jobs_considered: number;
  excluded_by_location: number;
  excluded_by_seniority: number;
  excluded_by_role: number;
  excluded_by_date: number;
  source_warnings: string[];
};

export type ScoreState = "pending" | "scoring" | "scored" | "failed" | "profile_incomplete";

export type JobMatchInfo = {
  fit_score: number | null;
  fit_label: string | null;
  score_state?: ScoreState | null;
  scoring_error_code?: string | null;
  match_reasons: string[];
  missing_skills: string[];
  risk_factors: string[];
  recommended_resume_angle: string | null;
  confidence: number | null;
  explanation_source: string | null;
  fit_summary?: string | null;
};

export type Job = {
  id: number;
  title: string;
  company: string;
  company_domain?: string | null;
  company_logo_url?: string | null;
  /** Relative path on the API — served through JobPilot's own cached, SSRF-safe
   * logo proxy. Preferred over company_logo_url when present. */
  company_logo_proxy_path?: string | null;
  source: string | null;
  location: string | null;
  workplace_type: string | null;
  employment_type: string | null;
  seniority_level: string | null;
  posted_at: string | null;
  discovered_at: string;
  application_url: string;
  source_url: string;
  description_clean: string;
  description_raw?: string;
  required_skills: string[];
  preferred_skills: string[];
  responsibilities?: string[];
  salary_min?: number | null;
  salary_max?: number | null;
  salary_currency?: string | null;
  match: JobMatchInfo | null;
  eligibility?: JobEligibility | null;
};

export type PeopleStatus =
  | "disabled"
  | "not_started"
  | "in_progress"
  | "partial"
  | "complete"
  | "no_reliable_matches"
  | "provider_unavailable"
  | "persistence_error"
  | "stale";

export type PeopleAvailabilityReason =
  | "available"
  | "globally_disabled"
  | "not_in_rollout"
  | "configuration_unavailable"
  | "provider_not_configured"
  | "provider_circuit_open"
  | "provider_unauthorized"
  | "provider_forbidden"
  | "provider_master_key_required_or_forbidden"
  | "provider_rate_limited"
  | "provider_timeout"
  | "provider_network_error"
  | "provider_schema_error"
  | "provider_request_invalid"
  | "provider_response_invalid"
  | "provider_budget_exceeded"
  | "provider_user_limit_exceeded"
  | "recommendation_commit_failed"
  | "provider_unavailable";

export type PeopleRecommendation = {
  recommendation_id: number;
  full_name: string;
  current_title: string;
  current_company: string;
  category: "likely_recruiter" | "potential_hiring_manager" | "potential_referrer";
  category_label: string;
  relevance_score: number;
  confidence: "high" | "moderate" | "limited";
  current_employment_confidence: number;
  employment_validation_status:
    | "confirmed_exact_company_verified"
    | "exact_company_current_but_unverified_freshness"
    | "confirmed_related_company"
    | "conflicting_current_employment"
    | "former_employee"
    | "stale_or_uncertain"
    | "insufficient_evidence";
  employment_last_verified_at: string | null;
  employment_warning: string | null;
  email_lookup_allowed: boolean;
  reasons: string[];
  limitations: string[];
  last_checked_at: string;
  professional_profile_url: string | null;
  email_status:
    | "not_requested"
    | "searching"
    | "verified"
    | "accept_all"
    | "risky"
    | "unknown"
    | "not_found"
    | "employment_conflict"
    | "identity_uncertain"
    | "rate_limited"
    | "budget_exceeded"
    | "provider_unavailable"
    | "provider_error";
  professional_email: string | null;
  email_verified_at: string | null;
  saved: boolean;
  contacted: boolean;
};

export type PeopleResponse = {
  status: PeopleStatus;
  availability_reason?: PeopleAvailabilityReason;
  retry_eligible?: boolean;
  retry_after_seconds?: number | null;
  retry_eligible_at?: string | null;
  beta: boolean;
  generated_at?: string | null;
  expires_at?: string | null;
  categories: {
    likely_recruiters: PeopleRecommendation[];
    potential_hiring_managers: PeopleRecommendation[];
    potential_referrers: PeopleRecommendation[];
  };
  warnings: string[];
  search_scope?: {
    company_scope: string;
    location_filter: "none" | "soft" | "hard";
    parent_company_matches_included: boolean;
    refresh_eligible: boolean;
    exact_company_search_completed?: boolean;
    related_company_search_attempted?: boolean;
    broaden_eligible?: boolean;
    broaden_attempted?: boolean;
  };
  controls: { email_discovery: boolean; outreach_drafting: boolean };
};

export type ProfileFilters = {
  target_roles: string[];
  target_levels: string[];
  preferred_locations: string[];
  work_preference: "everything" | "remote" | "hybrid" | "onsite";
};

export type DiscoverySummary = {
  fetched: number;
  fresh: number;
  persisted: number;
  matched: number;
  source_warnings: string[];
  eligible?: number;
  excluded_location?: number;
  excluded_seniority?: number;
  excluded_role?: number;
  excluded_other?: number;
  sources_searched?: number;
  sources_succeeded?: number;
  sources_failed?: number;
  by_provider?: Record<string, number>;
  packs?: string[];
};

export type JobEligibility = {
  eligible: boolean;
  reasons: string[];
  flags: Record<string, boolean | null>;
};

export type JobsResponse = {
  profile_complete: boolean;
  criteria: { role_queries: string[]; skills: string[]; seniority_targets: string[] };
  profile_filters: ProfileFilters;
  jobs: Job[];
  discovery?: DiscoverySummary;
};

export type ApiErrorCode =
  | "network_unreachable"
  | "auth_expired"
  | "not_found"
  | "validation"
  | "rate_limited"
  | "service_unavailable"
  | "server_error"
  | "request_failed";

/** A stable, machine-readable code from the backend's structured error envelope
 * (`{ error: { code, message, stage, retryable, request_id } }`). Used to show a
 * specific, actionable message instead of a generic one. */
export type ServerErrorCode =
  | "PROFILE_INCOMPLETE"
  | "JOB_NOT_FOUND"
  | "INVALID_APPLICATION_URL"
  | "DATABASE_UNAVAILABLE"
  | "INTERNAL_ERROR"
  | string;

/** Typed API error so the UI can distinguish a browser-level fetch failure from
 * a real backend response and show an actionable message. Extends Error, so
 * existing `err.message` handling keeps working. */
export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status?: number;
  readonly retryable: boolean;
  readonly details?: unknown;
  /** Backend structured-error fields, when present. */
  readonly serverCode?: ServerErrorCode;
  readonly stage?: string;
  readonly requestId?: string;

  constructor(args: {
    code: ApiErrorCode;
    message: string;
    status?: number;
    retryable: boolean;
    details?: unknown;
    serverCode?: ServerErrorCode;
    stage?: string;
    requestId?: string;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.code = args.code;
    this.status = args.status;
    this.retryable = args.retryable;
    this.details = args.details;
    this.serverCode = args.serverCode;
    this.stage = args.stage;
    this.requestId = args.requestId;
  }
}

type StructuredError = {
  code?: string;
  message?: string;
  stage?: string;
  retryable?: boolean;
  request_id?: string;
  details?: unknown;
};

function codeForStatus(status: number): { code: ApiErrorCode; retryable: boolean } {
  if (status === 401 || status === 403) return { code: "auth_expired", retryable: false };
  if (status === 404) return { code: "not_found", retryable: false };
  if (status === 400 || status === 422) return { code: "validation", retryable: false };
  if (status === 429) return { code: "rate_limited", retryable: true };
  if (status === 503) return { code: "service_unavailable", retryable: true };
  if (status >= 500) return { code: "server_error", retryable: true };
  return { code: "request_failed", retryable: false };
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = typeof window !== "undefined" ? window.localStorage.getItem("jobpilot_token") : null;
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers
      }
    });
  } catch (cause) {
    // fetch() rejects (TypeError) only when NO HTTP response was obtained:
    // backend down, wrong URL/port, DNS failure, or a response blocked by CORS.
    if (process.env.NODE_ENV === "development") {
      console.error(`[api] network failure for ${API_URL}${path}`, cause);
    }
    throw new ApiError({
      code: "network_unreachable",
      message:
        "JobPilot could not reach the application service. Confirm the backend is running and NEXT_PUBLIC_API_URL is set correctly.",
      retryable: true,
      details: cause instanceof Error ? cause.message : String(cause)
    });
  }

  if (!response.ok) {
    const body = await response.text();
    let message = body || `Request failed with ${response.status}`;
    let structured: StructuredError | undefined;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown; error?: StructuredError };
      // Preferred: the backend's structured error envelope.
      if (parsed.error && typeof parsed.error === "object") {
        structured = parsed.error;
        if (typeof structured.message === "string") message = structured.message;
      } else if (typeof parsed.detail === "string") {
        // Legacy shape used by most routes.
        message = parsed.detail;
      } else if (parsed.detail && typeof parsed.detail === "object") {
        // Several guarded endpoints use FastAPI's structured `detail` object.
        // Preserve its safe code/message just like the preferred envelope.
        structured = parsed.detail as StructuredError;
        if (typeof structured.message === "string") message = structured.message;
      }
    } catch {
      // Keep the raw body when the server does not return JSON.
    }
    const fallback = codeForStatus(response.status);
    throw new ApiError({
      code: fallback.code,
      message,
      status: response.status,
      // The server's own retry hint wins when it provides one.
      retryable: typeof structured?.retryable === "boolean" ? structured.retryable : fallback.retryable,
      serverCode: structured?.code,
      stage: structured?.stage,
      // The structured envelope carries it when present; every response also
      // carries the X-Request-ID header (see the API's catch_unhandled_errors
      // middleware), so a plain `detail` error still gets a correlation id the
      // user can quote in a support request.
      requestId: structured?.request_id ?? response.headers.get("x-request-id") ?? undefined
    });
  }
  return response.json() as Promise<T>;
}
