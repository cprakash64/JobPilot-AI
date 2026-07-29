"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  CircleAlert,
  Copy,
  ExternalLink,
  Linkedin,
  Loader2,
  Mail,
  MessageSquareText,
  X
} from "lucide-react";
import { api, ApiError, type PeopleRecommendation, type PeopleResponse } from "@/lib/api";
import {
  broadenPeople,
  discoverPeople,
  getCachedPeople,
  loadPeople,
  subscribeToPeople
} from "@/lib/peopleClient";
import { Button } from "@/components/Button";
import {
  derivePeopleView,
  formatResetTime,
  PEOPLE_MESSAGES,
  quotaSummary
} from "@/lib/peopleState";
import {
  buildMailtoUrl,
  copyText,
  openMailClient,
  safeEmailAddress,
  safeLinkedInUrl
} from "@/lib/outreachHandoff";

type JobId = string | number;
/**
 * Only two per-contact mutations remain. "Save contact" and "Mark contacted"
 * were removed from the UI: they were bookkeeping the user did not ask for, and
 * they crowded out the two things this panel exists to do — reach the person on
 * LinkedIn, or by email. The endpoints still exist server-side.
 */
type PersonAction = "email" | "incorrect";
type MessageType = "email" | "linkedin_connection_note" | "linkedin_message";
type DraftTone = "concise" | "warm" | "direct";
type OutreachDraftData = {
  message_type: MessageType;
  subject: string | null;
  body: string;
  facts_used: string[];
  assumptions: string[];
  omitted_uncertain_facts: string[];
  character_count: number;
  requires_manual_review: boolean;
  /** How the draft was produced. Every draft is template-built today. */
  generation_path?: string;
  template_version?: string;
  recipient_name?: string;
  recipient_category?: string;
  /** Supplied by the backend only when verified. Never derived client-side. */
  linkedin_url?: string | null;
  linkedin_available?: boolean;
  professional_email?: string | null;
  email_available?: boolean;
};
type DraftContext = {
  person: PeopleRecommendation;
  messageType: MessageType;
  tone: DraftTone;
  guidance: string;
};

const CATEGORY_HEADINGS: Array<[keyof PeopleResponse["categories"], string]> = [
  ["likely_recruiters", "Likely Recruiters"],
  ["potential_hiring_managers", "Potential Hiring Managers"],
  ["potential_referrers", "Potential Referral Candidates"]
];

function safeExternalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" &&
      (parsed.hostname === "linkedin.com" || parsed.hostname.endsWith(".linkedin.com")) &&
      parsed.pathname.startsWith("/in/")
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

function safePeopleError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (
    error.serverCode === "PEOPLE_GLOBAL_BUDGET_EXCEEDED" ||
    error.serverCode === "PEOPLE_USER_BUDGET_EXCEEDED"
  ) {
    return "People lookup is temporarily unavailable because a daily usage limit was reached.";
  }
  if (error.code === "auth_expired") {
    return "Your session has expired. Sign in again before loading people recommendations.";
  }
  if (error.serverCode === "PEOPLE_EMPLOYMENT_REVALIDATION_REQUIRED") {
    // Specific, and actionable: refreshing the search re-validates employment.
    return "This contact's current employment needs to be re-checked before drafting a message. Refresh people and try again.";
  }
  return fallback;
}

function peopleErrorCanRetry(error: unknown): boolean {
  return !(
    error instanceof ApiError &&
    (
      error.serverCode === "PEOPLE_GLOBAL_BUDGET_EXCEEDED" ||
      error.serverCode === "PEOPLE_USER_BUDGET_EXCEEDED"
    )
  );
}

function availabilityMessage(data: PeopleResponse): string | null {
  if (data.status !== "disabled") return null;
  if (data.availability_reason === "not_in_rollout") {
    return "People recommendations are currently available to selected beta users.";
  }
  if (data.availability_reason === "configuration_unavailable") {
    return "People recommendations are temporarily unavailable.";
  }
  return "People recommendations are not enabled for this account.";
}

/**
 * Every message names the real cause. The old catch-all "temporarily paused
 * after repeated provider failures" line is gone: it was shown for empty
 * results, unresolved domains, and per-user budgets, none of which are outages.
 */
function providerFailureMessage(data: PeopleResponse): string {
  const view = derivePeopleView({
    data,
    error: null,
    loading: false,
    requested: true
  });
  const message =
    data.availability_reason === "recommendation_commit_failed"
      ? PEOPLE_MESSAGES.persistence_error
      : view.message || PEOPLE_MESSAGES.provider_unavailable;
  if (view.state === "rate_limited" && view.retryAfterSeconds) {
    return `${message} Retry in about ${view.retryAfterSeconds} seconds.`;
  }
  return message;
}

function confidenceText(value: PeopleRecommendation["confidence"]) {
  return `${value[0].toUpperCase()}${value.slice(1)} confidence`;
}

function checkedDate(value?: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? null
    : parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function hasResults(data: PeopleResponse | null): data is PeopleResponse {
  return Boolean(
    data &&
    data.status !== "disabled" &&
    Object.values(data.categories).some((items) => items.length > 0)
  );
}

function usePeopleController(jobId: JobId, loadOnMount: boolean) {
  const [data, setData] = useState<PeopleResponse | null>(() => getCachedPeople(jobId));
  const [error, setError] = useState("");
  const [errorValue, setErrorValue] = useState<unknown>(null);
  const [errorCanRetry, setErrorCanRetry] = useState(true);
  const [loading, setLoading] = useState(loadOnMount);
  const [discovering, setDiscovering] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [draft, setDraft] = useState<OutreachDraftData | null>(null);
  const [draftContext, setDraftContext] = useState<DraftContext | null>(null);
  // Guards against a double-click or a rerender starting a second request.
  const discoveryInFlight = useRef(false);
  // Abort obsolete work when the card unmounts or the job it renders changes.
  const abortRef = useRef<AbortController | null>(null);

  const isCancelled = (value: unknown) =>
    value instanceof ApiError && value.code === "request_cancelled";

  const load = useCallback(async (force = true) => {
    setLoading(true);
    setError("");
    setErrorValue(null);
    try {
      const response = await loadPeople(jobId, force, abortRef.current?.signal);
      setData(response);
      return response;
    } catch (loadError) {
      if (isCancelled(loadError)) return null;
      setErrorValue(loadError);
      setErrorCanRetry(peopleErrorCanRetry(loadError));
      setError(
        safePeopleError(
          loadError,
          "People recommendations could not be loaded. Please try again."
        )
      );
      return null;
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    const unsubscribe = subscribeToPeople(jobId, setData);
    const loadTimer = loadOnMount
      ? window.setTimeout(() => void load(true), 0)
      : null;
    return () => {
      unsubscribe();
      controller.abort();
      if (loadTimer !== null) window.clearTimeout(loadTimer);
    };
  }, [jobId, loadOnMount, load]);

  const runDiscovery = useCallback(async (
    call: (signal?: AbortSignal) => Promise<PeopleResponse>,
    fallbackMessage: string
  ) => {
    if (discoveryInFlight.current) return null;
    discoveryInFlight.current = true;
    setDiscovering(true);
    setError("");
    setErrorValue(null);
    try {
      const response = await call(abortRef.current?.signal);
      setData(response);
      return response;
    } catch (discoverError) {
      if (isCancelled(discoverError)) return null;
      setErrorValue(discoverError);
      setErrorCanRetry(peopleErrorCanRetry(discoverError));
      setError(safePeopleError(discoverError, fallbackMessage));
      return null;
    } finally {
      discoveryInFlight.current = false;
      setDiscovering(false);
    }
  }, []);

  const discover = useCallback(
    () =>
      runDiscovery(
        (signal) => discoverPeople(jobId, signal),
        "People discovery could not be completed. Please try again."
      ),
    [jobId, runDiscovery]
  );

  const broaden = useCallback(
    () =>
      runDiscovery(
        (signal) => broadenPeople(jobId, signal),
        "The controlled broader search could not be completed. Please try again."
      ),
    [jobId, runDiscovery]
  );

  const personAction = useCallback(async (person: PeopleRecommendation, action: PersonAction) => {
    setActionId(person.recommendation_id);
    setError("");
    try {
      if (action === "incorrect") {
        await api(`/jobs/${jobId}/people/${person.recommendation_id}/feedback`, {
          method: "POST",
          body: JSON.stringify({
            information_correct_rating: "incorrect",
            employment_current_rating: "stale"
          })
        });
      } else {
        const response = await api<{
          status: PeopleRecommendation["email_status"];
          professional_email?: string | null;
          verified_at?: string | null;
        }>(`/jobs/${jobId}/people/${person.recommendation_id}/email`, {
          method: "POST"
        });
        setData((current) => current ? {
          ...current,
          categories: Object.fromEntries(
            Object.entries(current.categories).map(([key, people]) => [
              key,
              people.map((item) => item.recommendation_id === person.recommendation_id
                ? {
                    ...item,
                    email_status: response.status,
                    professional_email: response.professional_email ?? null,
                    email_verified_at: response.verified_at ?? null
                  }
                : item)
            ])
          ) as PeopleResponse["categories"]
        } : current);
        return;
      }
      await load(true);
    } catch (actionError) {
      setError(
        safePeopleError(
          actionError,
          action === "email"
            ? "Work-email lookup is temporarily unavailable. No email was displayed."
            : "The contact action could not be completed. Please try again."
        )
      );
    } finally {
      setActionId(null);
    }
  }, [jobId, load]);

  /**
   * Produces a grounded draft. `open` decides whether it lands in the review
   * dialog (the LinkedIn path, where the user copies it themselves) or is only
   * returned to the caller (the email path, which hands it to the mail client).
   */
  const draftOutreach = useCallback(async (
    person: PeopleRecommendation,
    messageType: MessageType,
    tone: DraftTone = "concise",
    guidance = "",
    open = true
  ): Promise<OutreachDraftData | null> => {
    setActionId(person.recommendation_id);
    setError("");
    try {
      const type =
        person.category === "potential_hiring_manager"
          ? "potential_hiring_manager_introduction"
          : person.category === "potential_referrer"
            ? "referrer_introduction"
            : "recruiter_introduction";
      const response = await api<OutreachDraftData>(
        `/jobs/${jobId}/people/${person.recommendation_id}/outreach-draft`,
        {
          method: "POST",
          body: JSON.stringify({
            draft_type: type,
            message_type: messageType,
            tone,
            user_guidance: guidance || null
          })
        }
      );
      if (open) {
        setDraft(response);
        setDraftContext({ person, messageType, tone, guidance });
      }
      return response;
    } catch (draftError) {
      setError(
        safePeopleError(
          draftError,
          "A grounded outreach draft could not be generated. Please try again."
        )
      );
      return null;
    } finally {
      setActionId(null);
    }
  }, [jobId]);

  return {
    data,
    error,
    errorValue,
    errorCanRetry,
    loading,
    discovering,
    actionId,
    draft,
    draftContext,
    setDraft,
    setDraftContext,
    load,
    discover,
    broaden,
    personAction,
    draftOutreach
  };
}

/** Every terminal failure status. Kept in one place so a new state cannot be
 * missed by one of the checks below. */
const PEOPLE_FAILURE_STATUSES: readonly string[] = [
  "provider_unavailable",
  "persistence_error",
  "domain_unresolved",
  "invalid_request",
  "user_budget_exhausted",
  "provider_budget_exhausted",
  "provider_configuration_error"
];

export function PeopleWhoCanHelp({ jobId }: { jobId: JobId }) {
  const titleId = useId();
  const controller = usePeopleController(jobId, true);
  const { data, error, errorCanRetry, loading, discovering } = controller;
  const availableMessage = data ? availabilityMessage(data) : null;
  const view = derivePeopleView({
    data,
    error: controller.errorValue,
    loading: loading || discovering,
    requested: Boolean(data && data.status !== "not_started")
  });
  // Reading the allowance never spends it, so it can be shown at any time.
  const quota = data?.quota;
  const resetLabel = formatResetTime(quota?.resets_at);
  const hasSearched = Boolean(data && data.status !== "not_started");
  const quotaLine = quota
    ? hasSearched && quota.daily_remaining > 0 && resetLabel
      ? `${quota.daily_remaining} searches remaining · Resets ${resetLabel}.`
      : quotaSummary(quota)
    : null;
  const counts = hasResults(data)
    ? [
        countLabel(data.categories.likely_recruiters.length, "recruiter"),
        countLabel(data.categories.potential_hiring_managers.length, "potential manager"),
        countLabel(data.categories.potential_referrers.length, "referral candidate")
      ].join(" · ")
    : "";
  const canDiscover =
    data?.availability_reason !== "not_in_rollout" &&
    data?.status !== "disabled" &&
    (
      data?.status === "not_started" ||
      data?.status === "stale" ||
      (
        PEOPLE_FAILURE_STATUSES.includes(data?.status ?? "") &&
        data?.retry_eligible !== false
      )
    );
  const canBroaden = Boolean(
    data?.status === "no_reliable_matches" &&
    data.search_scope?.broaden_eligible
  );

  return (
    <section aria-labelledby={titleId} className="mt-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 id={titleId} className="text-[13px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
              People Who Can Help
            </h2>
            {data?.beta ? (
              <span className="rounded-full border border-line px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
                Beta
              </span>
            ) : null}
          </div>
          <p className="mt-1.5 max-w-xl text-sm leading-6 text-[var(--text-muted)]">
            Contacts to research, with the evidence behind each one. Roles are potential matches, not confirmed
            assignments.
          </p>
          {counts ? (
            <p className="mt-2 text-xs font-medium text-[var(--text-secondary)]">{counts}</p>
          ) : null}
          {quotaLine ? <p className="mt-1 text-xs text-[var(--text-muted)]">{quotaLine}</p> : null}
        </div>
        {canDiscover || canBroaden ? (
          <Button
            onClick={() => void (canBroaden ? controller.broaden() : controller.discover())}
            disabled={discovering}
          >
            {discovering
              ? "Finding people…"
              : canBroaden
                ? "Broaden search"
                : data?.status === "stale"
                  ? "Refresh people"
                  : PEOPLE_FAILURE_STATUSES.includes(data?.status ?? "")
                    ? "Retry discovery"
                  : "Find people"}
          </Button>
        ) : null}
      </div>

      <div aria-live="polite" className="mt-4">
        {loading && !data ? (
          <p className="text-sm text-[var(--text-muted)]">Checking for saved results…</p>
        ) : null}
        {discovering || data?.status === "in_progress" ? (
          <p className="text-sm text-[var(--text-muted)]">Finding reliable professional matches…</p>
        ) : null}
        {availableMessage ? <p className="text-sm text-[var(--text-muted)]">{availableMessage}</p> : null}
        {error ? (
          <div>
            <p role="alert" className="text-sm text-red-700">{error}</p>
            {errorCanRetry ? (
              <Button variant="secondary" className="mt-3" onClick={() => void controller.load(true)}>
                Retry
              </Button>
            ) : null}
          </div>
        ) : null}
        {!PEOPLE_FAILURE_STATUSES.includes(data?.status ?? "") ? data?.warnings.map((warning) => (
          <p key={warning} className="mb-2 text-sm text-amber-800">{warning}</p>
        )) : null}
        {data?.status === "not_started" ? (
          <p className="text-sm text-[var(--text-muted)]">
            Find recruiters and referral candidates. Discovery runs only when you choose Find people.
          </p>
        ) : null}
        {view.cached && view.state !== "loading" ? (
          <p className="mb-2 text-xs text-[var(--text-muted)]">
            Showing previously saved results while the people provider is unavailable.
          </p>
        ) : null}
        {view.state === "partial" ? (
          <p className="mb-2 text-xs text-[var(--text-muted)]">{view.message}</p>
        ) : null}
        {data?.status === "no_reliable_matches" ? (
          <div>
            {/* A truthful empty answer, led by the neutral summary and followed
             * by what each category actually returned. */}
            <p className="mb-2 text-sm text-[var(--text-muted)]">{PEOPLE_MESSAGES.empty}</p>
            <EmptyPeopleCategories />
            {canBroaden ? (
              <>
                <p className="mt-3 text-xs text-[var(--text-muted)]">
                  Broaden search is optional and bounded. It may include broader titles or
                  evidence-backed related-company matches.
                </p>
                <p className="mt-1 text-xs font-medium text-[var(--text-secondary)]">
                  Broaden search uses {quota?.broadened_search_cost ?? 1} additional people search.
                </p>
              </>
            ) : null}
          </div>
        ) : null}
        {data && PEOPLE_FAILURE_STATUSES.includes(data.status) ? (
          <p className="text-sm text-[var(--text-muted)]">
            {providerFailureMessage(data)}
          </p>
        ) : null}
      </div>

      {/* Full transparency about how the search was run, one click away instead
        * of six lines of small print above the results. */}
      {data?.search_scope && data.status !== "disabled" ? (
        <details className="mt-4 text-xs text-[var(--text-muted)]">
          <summary className="focus-ring w-fit cursor-pointer rounded list-none font-medium marker:content-[''] hover:text-[var(--text-secondary)]">
            Search details
          </summary>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          <span>Scope: {data.search_scope.company_scope}</span>
          <span>Location used as a {data.search_scope.location_filter} signal</span>
          {checkedDate(data.generated_at) ? <span>Last checked: {checkedDate(data.generated_at)}</span> : null}
          <span>
            {data.search_scope.parent_company_matches_included
              ? "Related-company matches were considered"
              : "Related-company matches were not included"}
          </span>
          {data.search_scope.exact_company_search_completed ? (
            <span>Exact-company search completed</span>
          ) : null}
          {data.search_scope.broaden_attempted ? (
            <span>Controlled broader search completed</span>
          ) : null}
          <span>{data.search_scope.refresh_eligible ? "Refresh available" : "Using current cached search"}</span>
          </div>
        </details>
      ) : null}

      {hasResults(data) ? (
        <div className="mt-6 space-y-7">
          {CATEGORY_HEADINGS.map(([key, heading]) => {
            const people = data.categories[key];
            return (
              <section key={key} aria-labelledby={`${titleId}-${key}`}>
                <h3
                  id={`${titleId}-${key}`}
                  className="text-xs font-medium uppercase tracking-[0.08em] text-[var(--text-muted)]"
                >
                  {heading}
                </h3>
                {/* One column: categories usually hold one to three people, and
                  * a two-column grid left ragged half-empty rows. */}
                {people.length ? (
                  <div className="mt-3 grid gap-3">
                    {people.map((person) => (
                      <PersonCard
                        key={person.recommendation_id}
                        person={person}
                        busy={controller.actionId === person.recommendation_id}
                        emailEnabled={data.controls.email_discovery}
                        outreachEnabled={data.controls.outreach_drafting}
                        onAction={controller.personAction}
                        onDraft={controller.draftOutreach}
                      />
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-[var(--text-muted)]">
                    {key === "likely_recruiters"
                      ? "No reliable recruiting contact met JobPilot’s threshold."
                      : key === "potential_hiring_managers"
                        ? "No potential manager met JobPilot’s confidence threshold."
                        : "No relevant employee met JobPilot’s referral threshold."}
                  </p>
                )}
              </section>
            );
          })}
        </div>
      ) : null}

      <OutreachDraft
        draft={controller.draft}
        context={controller.draftContext}
        setDraft={controller.setDraft}
        setContext={controller.setDraftContext}
        regenerate={controller.draftOutreach}
      />
    </section>
  );
}


function EmptyPeopleCategories() {
  return (
    <div className="grid gap-2 text-sm text-[var(--text-muted)]">
      <p>No reliable recruiting contact met JobPilot’s threshold.</p>
      <p>No potential hiring manager met JobPilot’s threshold.</p>
      <p>No relevant employee met JobPilot’s referral threshold.</p>
    </div>
  );
}


/**
 * One contact, at a glance: who they are, why they are here, and the two ways
 * to reach them. Everything else that used to live on this card — bookkeeping
 * buttons, a stack of amber caveats, three evidence bullets — either moved into
 * one quiet line or left.
 */
function PersonCard({
  person,
  busy,
  emailEnabled,
  outreachEnabled,
  onAction,
  onDraft
}: {
  person: PeopleRecommendation;
  busy: boolean;
  emailEnabled: boolean;
  outreachEnabled: boolean;
  onAction: (person: PeopleRecommendation, action: PersonAction) => Promise<void>;
  onDraft: (
    person: PeopleRecommendation,
    messageType: MessageType,
    tone?: DraftTone,
    guidance?: string,
    open?: boolean
  ) => Promise<OutreachDraftData | null>;
}) {
  const [status, setStatus] = useState("");
  const profileUrl = safeExternalUrl(person.professional_profile_url);
  const verifiedEmail = safeEmailAddress(
    person.email_status === "verified" ? person.professional_email : null
  );
  const canLookUpEmail =
    emailEnabled &&
    person.email_lookup_allowed &&
    ["not_requested", "provider_error", "provider_unavailable"].includes(person.email_status);
  const emailNote = emailStateNote(person);

  /**
   * Hands the draft to the user's own mail client. The address is only ever the
   * backend-verified one, and the mail client is where the user reads, edits,
   * and sends — JobPilot never sends anything itself.
   */
  async function openEmail() {
    if (!verifiedEmail) return;
    setStatus("");
    const draft = outreachEnabled
      ? await onDraft(person, "email", "concise", "", false)
      : null;
    openMailClient(
      buildMailtoUrl({
        address: verifiedEmail,
        subject: draft?.subject ?? null,
        body: draft?.body ?? null
      })
    );
    setStatus(
      draft
        ? "Opened your email app with a draft. Review it before sending."
        : "Opened your email app. Nothing is sent until you send it."
    );
  }

  return (
    <article className="rounded-2xl border border-line bg-white p-4 transition-colors hover:border-border-strong sm:p-5">
      <div className="flex items-start gap-3.5">
        <PersonAvatar name={person.full_name} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h4 className="truncate text-[15px] font-semibold leading-tight text-[var(--text-primary)]">
                {person.full_name}
              </h4>
              <p className="mt-1 truncate text-sm text-[var(--text-secondary)]">{person.current_title}</p>
              <p className="truncate text-sm text-[var(--text-muted)]">{person.current_company}</p>
            </div>
            <CategoryBadge person={person} />
          </div>
        </div>
      </div>

      {/* Confidence and freshness in one line instead of four. */}
      <p className="mt-3.5 text-xs leading-5 text-[var(--text-muted)]">{evidenceLine(person)}</p>
      {/* The caveats stay — quietly. A grey line is still read; four amber
        * paragraphs per card were skipped. */}
      {[person.employment_warning, person.limitations[0]].filter(Boolean).map((note) => (
        <p key={note} className="mt-1.5 flex gap-1.5 text-xs leading-5 text-[var(--text-muted)]">
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{note}</span>
        </p>
      ))}

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line/70 pt-3.5">
        {profileUrl ? (
          <a
            className="focus-ring inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:bg-panel"
            href={profileUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Linkedin className="h-4 w-4" aria-hidden /> LinkedIn
            <ExternalLink className="h-3 w-3 opacity-60" aria-hidden />
          </a>
        ) : (
          <span
            className="inline-flex h-9 cursor-not-allowed items-center gap-2 rounded-lg border border-line/70 px-3 text-sm text-[var(--text-muted)] opacity-70"
            title="JobPilot never guesses a profile URL. None was verified for this contact."
          >
            <Linkedin className="h-4 w-4" aria-hidden /> No LinkedIn
          </span>
        )}

        {verifiedEmail ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void openEmail()}
            className="focus-ring inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:bg-panel disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Mail className="h-4 w-4" aria-hidden />}
            Email
          </button>
        ) : canLookUpEmail ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onAction(person, "email")}
            className="focus-ring inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:bg-panel disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Mail className="h-4 w-4" aria-hidden />}
            {person.email_status === "provider_error" ? "Retry work email" : "Find work email"}
          </button>
        ) : (
          <span
            className="inline-flex h-9 cursor-not-allowed items-center gap-2 rounded-lg border border-line/70 px-3 text-sm text-[var(--text-muted)] opacity-70"
            title={emailNote ?? "No verified work email is available for this contact."}
          >
            <Mail className="h-4 w-4" aria-hidden /> No email
          </span>
        )}

        {outreachEnabled ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onDraft(person, "linkedin_message")}
            className="focus-ring inline-flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm font-medium text-pine transition-colors hover:bg-[var(--success-surface)] disabled:opacity-50"
          >
            <MessageSquareText className="h-4 w-4" aria-hidden /> Draft message
          </button>
        ) : null}
      </div>

      {/* A disabled control needs a reason the user can actually read — a title
        * attribute alone is invisible to keyboard and touch. */}
      {emailNote && !verifiedEmail ? (
        <p className="mt-2.5 text-xs text-[var(--text-muted)]">{emailNote}</p>
      ) : null}
      {verifiedEmail ? (
        <p className="mt-2.5 truncate text-xs text-[var(--text-muted)]">Verified work email: {verifiedEmail}</p>
      ) : null}
      {status ? (
        <p aria-live="polite" className="mt-2 text-xs text-[var(--text-muted)]">
          {status}
        </p>
      ) : null}

      <button
        type="button"
        disabled={busy}
        onClick={() => void onAction(person, "incorrect")}
        className="focus-ring mt-3 rounded text-xs text-[var(--text-muted)] underline-offset-2 hover:underline disabled:opacity-50"
      >
        Report incorrect information
      </button>
    </article>
  );
}

/**
 * The provider exposes no profile photograph, so this is a deterministic
 * initials mark rather than an avatar that pretends to be one.
 */
function PersonAvatar({ name }: { name: string }) {
  const initials = name
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <span
      aria-hidden
      className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-line bg-panel text-sm font-semibold text-[var(--text-secondary)]"
    >
      {initials || "?"}
    </span>
  );
}

/** Short badge; the section heading above it carries the "potential" framing. */
function CategoryBadge({ person }: { person: PeopleRecommendation }) {
  const label =
    person.category === "likely_recruiter"
      ? "Recruiter"
      : person.category === "potential_hiring_manager"
        ? "Hiring manager"
        : "Referral";
  const recruiter = person.category === "likely_recruiter";
  return (
    <span
      title={person.category_label}
      className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${
        recruiter
          ? "bg-[var(--success-surface)] text-[var(--success)]"
          : "border border-line text-[var(--text-muted)]"
      }`}
    >
      {label}
    </span>
  );
}

/** Confidence, verification date, and the top piece of evidence in one line. */
function evidenceLine(person: PeopleRecommendation): string {
  const parts = [confidenceText(person.confidence)];
  const verified = checkedDate(person.employment_last_verified_at);
  parts.push(verified ? `employment verified ${verified}` : "employment not independently verified");
  const reason = person.reasons[0];
  return reason ? `${parts.join(" · ")} · ${reason}` : parts.join(" · ");
}

/** One sentence per email state, or nothing when there is nothing to say. */
function emailStateNote(person: PeopleRecommendation): string | null {
  switch (person.email_status) {
    case "searching":
      return "Searching for a verified work email…";
    case "accept_all":
    case "risky":
    case "unknown":
      return "A work email could not be verified, so none is shown.";
    case "not_found":
      return "No work email was found.";
    case "provider_error":
    case "provider_unavailable":
      return "The work-email provider is temporarily unavailable.";
    case "rate_limited":
      return "The work-email provider rate limit has been reached.";
    case "budget_exceeded":
      return "Work-email lookup is unavailable because its daily limit was reached.";
    case "employment_conflict":
      return "Work email is unavailable until current employment is revalidated.";
    case "identity_uncertain":
      return "Work email is unavailable because the professional identity is uncertain.";
    default:
      return null;
  }
}

function OutreachDraft({
  draft,
  context,
  setDraft,
  setContext,
  regenerate
}: {
  draft: OutreachDraftData | null;
  context: DraftContext | null;
  setDraft: (value: OutreachDraftData | null) => void;
  setContext: (value: DraftContext | null) => void;
  regenerate: (
    person: PeopleRecommendation,
    messageType: MessageType,
    tone?: DraftTone,
    guidance?: string,
    open?: boolean
  ) => Promise<OutreachDraftData | null>;
}) {
  const [copied, setCopied] = useState<"subject" | "body" | null>(null);

  if (!draft || !context) return null;

  const close = () => {
    setDraft(null);
    setContext(null);
    setCopied(null);
  };

  // Both handoff targets come from the backend's verified fields. Nothing here
  // constructs a profile URL or an address from a name or a company domain.
  const linkedInUrl = safeLinkedInUrl(
    draft.linkedin_url ?? context.person.professional_profile_url
  );
  const emailAddress = safeEmailAddress(
    draft.professional_email ?? context.person.professional_email
  );
  const isEmail = draft.message_type === "email";
  const recipient = draft.recipient_name ?? context.person.full_name;
  const channelLabel = isEmail
    ? "Email"
    : draft.message_type === "linkedin_connection_note"
      ? "LinkedIn connection note"
      : "LinkedIn message";

  async function copy(kind: "subject" | "body", value: string) {
    const ok = await copyText(value);
    setCopied(ok ? kind : null);
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="outreach-draft-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="max-h-full w-full max-w-xl overflow-y-auto rounded-xl bg-[var(--surface)] p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <div>
            <h3 id="outreach-draft-title" className="text-lg font-semibold">Review outreach draft</h3>
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              {channelLabel} to {recipient}
            </p>
          </div>
          <button aria-label="Close outreach draft" onClick={close}><X /></button>
        </div>
        {draft.generation_path === "deterministic_template" ? (
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            Generated from a verified template.
          </p>
        ) : null}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            Tone
            <select
              aria-label="Draft tone"
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] p-2"
              value={context.tone}
              onChange={(event) => setContext({
                ...context,
                tone: event.target.value as DraftTone
              })}
            >
              <option value="concise">Concise</option>
              <option value="warm">Warm</option>
              <option value="direct">Direct</option>
            </select>
          </label>
          <label className="text-sm">
            Optional guidance
            <input
              aria-label="Draft guidance"
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] p-2"
              value={context.guidance}
              onChange={(event) => setContext({ ...context, guidance: event.target.value })}
              placeholder="Add a fact or preference"
            />
          </label>
        </div>
        {draft.subject !== null ? (
          <div className="mt-4">
            <label className="block text-sm">
              Subject
              <input
                aria-label="Outreach subject"
                className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] p-2"
                value={draft.subject}
                onChange={(event) => setDraft({ ...draft, subject: event.target.value })}
              />
            </label>
            <Button
              variant="secondary"
              className="mt-2"
              onClick={() => void copy("subject", draft.subject ?? "")}
            >
              <Copy className="h-4 w-4" /> {copied === "subject" ? "Subject copied" : "Copy subject"}
            </Button>
          </div>
        ) : null}
        <textarea
          aria-label="Outreach draft"
          className="mt-4 min-h-56 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] p-3"
          value={draft.body}
          onChange={(event) => setDraft({
            ...draft,
            body: event.target.value,
            character_count: event.target.value.length
          })}
        />
        <p className="mt-1 text-xs text-[var(--text-muted)]">{draft.character_count} characters</p>

        <div className="mt-4 flex flex-wrap gap-2 border-t border-[var(--border)] pt-4">
          <Button variant="secondary" onClick={() => void copy("body", draft.body)}>
            <Copy className="h-4 w-4" /> {copied === "body" ? "Message copied" : "Copy message"}
          </Button>
          {isEmail ? (
            <Button
              variant="secondary"
              disabled={!emailAddress}
              onClick={() => {
                if (!emailAddress) return;
                openMailClient(
                  buildMailtoUrl({
                    address: emailAddress,
                    subject: draft.subject,
                    body: draft.body
                  })
                );
              }}
            >
              <Mail className="h-4 w-4" /> Open email app
            </Button>
          ) : linkedInUrl ? (
            // LinkedIn accepts no prefilled message in a profile URL, so the
            // honest handoff is: copy the text, open the profile, paste. A real
            // anchor keeps the navigation immune to the async copy.
            <a
              href={linkedInUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => void copy("body", draft.body)}
              className="focus-ring inline-flex h-10 items-center justify-center gap-2 rounded-md border border-line bg-white px-4 text-sm font-medium text-ink transition hover:bg-panel"
            >
              <ExternalLink className="h-4 w-4" /> Copy and open LinkedIn
            </a>
          ) : (
            <Button variant="secondary" disabled>
              <ExternalLink className="h-4 w-4" /> Open LinkedIn
            </Button>
          )}
          <Button
            variant="secondary"
            onClick={() => void regenerate(
              context.person,
              context.messageType,
              context.tone,
              context.guidance
            )}
          >
            Regenerate draft
          </Button>
          <Button variant="secondary" onClick={close}>Close</Button>
        </div>
        {isEmail && !emailAddress ? (
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            Verified email unavailable. You can still copy this message and send it yourself.
          </p>
        ) : null}
        {!isEmail && !linkedInUrl ? (
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            LinkedIn profile URL is unavailable for this contact. You can still copy this message.
          </p>
        ) : null}
        {!isEmail && linkedInUrl ? (
          <p className="mt-2 text-sm text-[var(--text-muted)]">
            LinkedIn does not accept a prefilled message in a profile link, so the draft is copied to your
            clipboard for you to paste.
          </p>
        ) : null}

        {draft.facts_used.length ? (
          <p className="mt-3 text-xs text-[var(--text-muted)]">
            Grounded in: {draft.facts_used.join("; ")}
          </p>
        ) : null}
        {draft.omitted_uncertain_facts.length ? (
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Omitted as uncertain: {draft.omitted_uncertain_facts.join("; ")}
          </p>
        ) : null}
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          Review and edit before manually sending. JobPilot never sends this message automatically.
        </p>
      </div>
    </div>
  );
}

function countLabel(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}
