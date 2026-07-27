"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ExternalLink, Mail, MessageSquareText, Star, UserCheck, Users, X } from "lucide-react";
import { api, ApiError, type PeopleRecommendation, type PeopleResponse } from "@/lib/api";
import {
  broadenPeople,
  discoverPeople,
  getCachedPeople,
  loadPeople,
  subscribeToPeople
} from "@/lib/peopleClient";
import { Button } from "@/components/Button";

type JobId = string | number;
type PersonAction = "email" | "save" | "unsave" | "contacted" | "incorrect";
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

function providerFailureMessage(data: PeopleResponse): string {
  const messages: Record<string, string> = {
    provider_unauthorized: "The people data provider credentials could not be verified.",
    provider_forbidden: "The configured provider account does not have access to people search.",
    provider_rate_limited: "The people data provider rate limit has been reached.",
    provider_timeout: "The people search provider took too long to respond.",
    provider_circuit_open: "People search is temporarily paused after repeated provider failures.",
    provider_schema_error: "The people provider returned an unsupported response."
  };
  const message = messages[data.availability_reason ?? ""] ??
    "The professional data provider is temporarily unavailable. You can safely retry later.";
  if (
    data.availability_reason === "provider_circuit_open" &&
    data.retry_eligible !== false &&
    data.retry_after_seconds
  ) {
    return `${message} Retry is available in about ${data.retry_after_seconds} seconds.`;
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
  const [errorCanRetry, setErrorCanRetry] = useState(true);
  const [loading, setLoading] = useState(loadOnMount);
  const [discovering, setDiscovering] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [draft, setDraft] = useState<OutreachDraftData | null>(null);
  const [draftContext, setDraftContext] = useState<DraftContext | null>(null);
  const discoveryInFlight = useRef(false);

  const load = useCallback(async (force = true) => {
    setLoading(true);
    setError("");
    try {
      const response = await loadPeople(jobId, force);
      setData(response);
      return response;
    } catch (loadError) {
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
    const unsubscribe = subscribeToPeople(jobId, setData);
    const loadTimer = loadOnMount
      ? window.setTimeout(() => void load(true), 0)
      : null;
    return () => {
      unsubscribe();
      if (loadTimer !== null) window.clearTimeout(loadTimer);
    };
  }, [jobId, loadOnMount, load]);

  const discover = useCallback(async () => {
    if (discoveryInFlight.current) return null;
    discoveryInFlight.current = true;
    setDiscovering(true);
    setError("");
    try {
      const response = await discoverPeople(jobId);
      setData(response);
      return response;
    } catch (discoverError) {
      setErrorCanRetry(peopleErrorCanRetry(discoverError));
      setError(
        safePeopleError(
          discoverError,
          "People discovery could not be completed. Please try again."
        )
      );
      return null;
    } finally {
      discoveryInFlight.current = false;
      setDiscovering(false);
    }
  }, [jobId]);

  const broaden = useCallback(async () => {
    if (discoveryInFlight.current) return null;
    discoveryInFlight.current = true;
    setDiscovering(true);
    setError("");
    try {
      const response = await broadenPeople(jobId);
      setData(response);
      return response;
    } catch (broadenError) {
      setErrorCanRetry(peopleErrorCanRetry(broadenError));
      setError(
        safePeopleError(
          broadenError,
          "The controlled broader search could not be completed. Please try again."
        )
      );
      return null;
    } finally {
      discoveryInFlight.current = false;
      setDiscovering(false);
    }
  }, [jobId]);

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
      } else if (action === "email") {
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
      } else {
        const suffix = action === "unsave" ? "save" : action;
        await api(`/jobs/${jobId}/people/${person.recommendation_id}/${suffix}`, {
          method: action === "unsave" ? "DELETE" : "POST"
        });
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

  const draftOutreach = useCallback(async (
    person: PeopleRecommendation,
    messageType: MessageType,
    tone: DraftTone = "concise",
    guidance = ""
  ) => {
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
      setDraft(response);
      setDraftContext({ person, messageType, tone, guidance });
    } catch (draftError) {
      setError(
        safePeopleError(
          draftError,
          "A grounded outreach draft could not be generated. Please try again."
        )
      );
    } finally {
      setActionId(null);
    }
  }, [jobId]);

  return {
    data,
    error,
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

export function PeopleWhoCanHelp({ jobId }: { jobId: JobId }) {
  const titleId = useId();
  const controller = usePeopleController(jobId, true);
  const { data, error, errorCanRetry, loading, discovering } = controller;
  const availableMessage = data ? availabilityMessage(data) : null;
  const canDiscover =
    data?.availability_reason !== "not_in_rollout" &&
    data?.status !== "disabled" &&
    (
      data?.status === "not_started" ||
      data?.status === "stale" ||
      (data?.status === "provider_unavailable" && data.retry_eligible !== false)
    );
  const canBroaden = Boolean(
    data?.status === "no_reliable_matches" &&
    data.search_scope?.broaden_eligible
  );

  return (
    <section
      aria-labelledby={titleId}
      className="mt-6 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 id={titleId} className="text-xl font-semibold">People Who Can Help</h2>
            {data?.beta ? (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900">
                Beta
              </span>
            ) : null}
          </div>
          <p className="mt-1 max-w-2xl text-sm text-[var(--text-muted)]">
            Evidence-based professional contacts to research. Roles are potential matches, not
            confirmed assignments. You choose whether and how to contact anyone.
          </p>
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
        {data?.status !== "provider_unavailable" ? data?.warnings.map((warning) => (
          <p key={warning} className="mb-2 text-sm text-amber-800">{warning}</p>
        )) : null}
        {data?.status === "not_started" ? (
          <p className="text-sm text-[var(--text-muted)]">
            Find recruiters and referral candidates. Discovery runs only when you choose Find people.
          </p>
        ) : null}
        {data?.status === "no_reliable_matches" ? (
          <div>
            <EmptyPeopleCategories />
            {canBroaden ? (
              <p className="mt-3 text-xs text-[var(--text-muted)]">
                Broaden search is optional and bounded. It may include broader titles or
                evidence-backed related-company matches.
              </p>
            ) : null}
          </div>
        ) : null}
        {data?.status === "provider_unavailable" ? (
          <p className="text-sm text-[var(--text-muted)]">
            {providerFailureMessage(data)}
          </p>
        ) : null}
      </div>

      {data?.search_scope && data.status !== "disabled" ? (
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
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
      ) : null}

      {hasResults(data) ? (
        <div className="mt-5 space-y-6">
          {CATEGORY_HEADINGS.map(([key, heading]) => {
            const people = data.categories[key];
            return (
              <section key={key} aria-labelledby={`${titleId}-${key}`}>
                <h3 id={`${titleId}-${key}`} className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                  {heading}
                </h3>
                {people.length ? (
                  <div className="mt-2 grid gap-3 lg:grid-cols-2">
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

export function PeopleWhoCanHelpSummary({
  jobId,
  onViewAll
}: {
  jobId: JobId;
  onViewAll: () => void;
}) {
  const titleId = useId();
  const [expanded, setExpanded] = useState(false);
  const controller = usePeopleController(jobId, false);
  const { data, error, errorCanRetry, loading, discovering } = controller;
  const resultsAvailable = hasResults(data);
  const availableMessage = data ? availabilityMessage(data) : null;

  async function loadAndMaybeDiscover(force: boolean) {
    setExpanded(true);
    const loaded = data && !force ? data : await controller.load(force);
    if (loaded?.status === "not_started") await controller.discover();
  }

  async function activate() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    await loadAndMaybeDiscover(false);
  }

  const counts = data
    ? [
        countLabel(data.categories.likely_recruiters.length, "recruiter"),
        countLabel(data.categories.potential_hiring_managers.length, "potential manager"),
        countLabel(data.categories.potential_referrers.length, "referral candidate")
      ].join(" · ")
    : "";

  const compactPeople = data
    ? [
        ...data.categories.likely_recruiters.slice(0, 1),
        ...data.categories.potential_hiring_managers.slice(0, 1),
        ...data.categories.potential_referrers.slice(0, 2)
      ]
    : [];

  return (
    <section aria-labelledby={titleId} className="mt-4 rounded-xl border border-line bg-panel/30 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-pine" />
            <h3 id={titleId} className="text-sm font-semibold">People Who Can Help</h3>
            {data?.beta ? (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-900">
                Beta
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Find relevant recruiters, hiring-team members, and potential referrals.
          </p>
          {resultsAvailable ? (
            <p className="mt-2 text-xs font-medium text-[var(--text-secondary)]">{counts}</p>
          ) : null}
        </div>
        <Button
          variant="secondary"
          aria-expanded={expanded}
          onClick={() => void activate()}
          disabled={loading || discovering}
        >
          {loading || discovering
            ? "Finding people…"
            : expanded
              ? "Hide people"
              : resultsAvailable
                ? "View people"
                : "Find people"}
        </Button>
      </div>

      {expanded ? (
        <div aria-live="polite" className="mt-4 border-t border-line/70 pt-4">
          {availableMessage ? <p className="text-sm text-[var(--text-muted)]">{availableMessage}</p> : null}
          {error ? (
            <div>
              <p role="alert" className="text-sm text-red-700">{error}</p>
              {errorCanRetry ? (
                <Button variant="secondary" className="mt-3" onClick={() => void loadAndMaybeDiscover(true)}>
                  Retry
                </Button>
              ) : null}
            </div>
          ) : null}
          {data?.status === "provider_unavailable" && data.retry_eligible !== false ? (
            <StateWithRetry
              text={providerFailureMessage(data)}
              onRetry={() => void controller.discover()}
            />
          ) : null}
          {data?.status === "provider_unavailable" && data.retry_eligible === false ? (
            <p className="text-sm text-[var(--text-muted)]">
              {providerFailureMessage(data)}
            </p>
          ) : null}
          {data?.status === "stale" && !discovering ? (
            <StateWithRetry
              text="Saved people results need revalidation."
              onRetry={() => void controller.discover()}
              label="Refresh people"
            />
          ) : null}
          {loading && !data ? (
            <p className="text-sm text-[var(--text-muted)]">Checking for saved results…</p>
          ) : null}
          {data?.status === "no_reliable_matches" ? (
            <div>
              <EmptyPeopleCategories />
              {data.search_scope?.broaden_eligible ? (
                <>
                  <p className="mt-3 text-xs text-[var(--text-muted)]">
                    A controlled broader search may include broader titles or
                    evidence-backed related-company matches.
                  </p>
                  <Button
                    variant="secondary"
                    className="mt-3"
                    onClick={() => void controller.broaden()}
                    disabled={discovering}
                  >
                    Broaden search
                  </Button>
                </>
              ) : null}
            </div>
          ) : null}
          {data?.status === "not_started" && !discovering ? (
            <StateWithRetry
              text="Find recruiters and referral candidates for this job."
              onRetry={() => void controller.discover()}
              label="Find people"
            />
          ) : null}
          {discovering || data?.status === "in_progress" ? (
            <p className="text-sm text-[var(--text-muted)]">Finding reliable professional matches…</p>
          ) : null}
          {compactPeople.length ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {compactPeople.map((person) => (
                <CompactPerson
                  key={person.recommendation_id}
                  person={person}
                  busy={controller.actionId === person.recommendation_id}
                  emailEnabled={Boolean(data?.controls.email_discovery)}
                  onEmail={() => void controller.personAction(person, "email")}
                />
              ))}
            </div>
          ) : null}
          {resultsAvailable ? (
            <Button variant="secondary" className="mt-4" onClick={onViewAll}>
              View all people
            </Button>
          ) : null}
        </div>
      ) : null}
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

function StateWithRetry({
  text,
  onRetry,
  label = "Retry discovery"
}: {
  text: string;
  onRetry: () => void;
  label?: string;
}) {
  return (
    <div>
      <p className="text-sm text-[var(--text-muted)]">{text}</p>
      <Button variant="secondary" className="mt-3" onClick={onRetry}>{label}</Button>
    </div>
  );
}

function CompactPerson({
  person,
  busy,
  emailEnabled,
  onEmail
}: {
  person: PeopleRecommendation;
  busy: boolean;
  emailEnabled: boolean;
  onEmail: () => void;
}) {
  const profileUrl = safeExternalUrl(person.professional_profile_url);
  return (
    <article className="rounded-lg border border-line bg-white p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        {person.category_label}
      </p>
      <h4 className="mt-1 font-semibold">{person.full_name}</h4>
      <p className="text-sm text-[var(--text-secondary)]">{person.current_title}</p>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Current employment confidence: {Math.round(person.current_employment_confidence * 100)}%
        {checkedDate(person.employment_last_verified_at)
          ? ` · verified ${checkedDate(person.employment_last_verified_at)}`
          : ""}
      </p>
      {person.employment_warning ? (
        <p className="mt-2 text-sm text-amber-800">{person.employment_warning}</p>
      ) : null}
      <EmailState person={person} />
      <div className="mt-3 flex flex-wrap gap-2">
        {profileUrl ? (
          <a
            className="inline-flex items-center gap-1 rounded-md border border-line px-3 py-2 text-sm"
            href={profileUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            LinkedIn profile <ExternalLink className="h-4 w-4" />
          </a>
        ) : null}
        {emailEnabled && person.email_lookup_allowed &&
        ["not_requested", "provider_error", "provider_unavailable"].includes(person.email_status) ? (
          <button
            className="rounded-md border border-line px-3 py-2 text-sm"
            disabled={busy}
            onClick={onEmail}
          >
            <Mail className="mr-1 inline h-4 w-4" />
            {person.email_status === "provider_error" ? "Retry work email" : "Find work email"}
          </button>
        ) : null}
      </div>
    </article>
  );
}

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
    messageType: MessageType
  ) => Promise<void>;
}) {
  const profileUrl = safeExternalUrl(person.professional_profile_url);
  return (
    <article className="rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="font-semibold">{person.full_name}</h4>
          <p className="text-sm">{person.current_title} · {person.current_company}</p>
        </div>
        <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs">{person.category_label}</span>
      </div>
      <p className="mt-2 text-xs font-medium text-[var(--text-muted)]">{confidenceText(person.confidence)}</p>
      <p className="mt-1 text-xs font-medium text-[var(--text-muted)]">
        Current employment confidence: {Math.round(person.current_employment_confidence * 100)}%
      </p>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Employment last verified {checkedDate(person.employment_last_verified_at) ?? "date unavailable"}
      </p>
      {person.employment_warning ? (
        <p className="mt-2 text-sm text-amber-800">{person.employment_warning}</p>
      ) : null}
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
        {person.reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}
      </ul>
      {person.limitations[0] ? (
        <p className="mt-3 text-sm text-amber-800"><span className="font-medium">Limitation:</span> {person.limitations[0]}</p>
      ) : null}
      <p className="mt-3 text-xs text-[var(--text-muted)]">
        Last checked {new Date(person.last_checked_at).toLocaleDateString()}
      </p>
      <EmailState person={person} />
      <div className="mt-4 flex flex-wrap gap-2">
        {profileUrl ? (
          <a
            className="inline-flex items-center gap-1 rounded-md border border-[var(--border)] px-3 py-2 text-sm"
            href={profileUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            LinkedIn profile <ExternalLink className="h-4 w-4" />
          </a>
        ) : null}
        {emailEnabled && person.email_lookup_allowed &&
        ["not_requested", "provider_error", "provider_unavailable"].includes(person.email_status) ? (
          <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm" disabled={busy} onClick={() => void onAction(person, "email")}>
            <Mail className="mr-1 inline h-4 w-4" />
            {person.email_status === "provider_error" ? "Retry work email" : "Find work email"}
          </button>
        ) : null}
        {outreachEnabled ? (
          <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm" disabled={busy} onClick={() => void onDraft(person, "linkedin_message")}>
            <MessageSquareText className="mr-1 inline h-4 w-4" /> Draft LinkedIn message
          </button>
        ) : null}
        {outreachEnabled ? (
          <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm" disabled={busy} onClick={() => void onDraft(person, "email")}>
            <Mail className="mr-1 inline h-4 w-4" /> Draft email
          </button>
        ) : null}
        <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm" disabled={busy} aria-pressed={person.saved} onClick={() => void onAction(person, person.saved ? "unsave" : "save")}>
          <Star className="mr-1 inline h-4 w-4" /> {person.saved ? "Saved" : "Save contact"}
        </button>
        <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm" disabled={busy || person.contacted} onClick={() => void onAction(person, "contacted")}>
          <UserCheck className="mr-1 inline h-4 w-4" /> {person.contacted ? "Contacted" : "Mark contacted"}
        </button>
        <button className="px-2 py-2 text-sm text-red-700" disabled={busy} onClick={() => void onAction(person, "incorrect")}>
          Report incorrect information
        </button>
      </div>
    </article>
  );
}

function EmailState({ person }: { person: PeopleRecommendation }) {
  if (person.email_status === "verified" && person.professional_email) {
    return (
      <p className="mt-2 text-sm">
        <Mail className="mr-1 inline h-4 w-4" />
        Verified work email: {person.professional_email}
      </p>
    );
  }
  if (person.email_status === "searching") {
    return <p className="mt-2 text-sm text-[var(--text-muted)]">Searching for a verified work email…</p>;
  }
  if (["accept_all", "risky", "unknown"].includes(person.email_status)) {
    return <p className="mt-2 text-sm text-amber-800">A work email was not verified and is not displayed.</p>;
  }
  if (person.email_status === "not_found") {
    return <p className="mt-2 text-sm text-[var(--text-muted)]">Work email not found.</p>;
  }
  if (person.email_status === "provider_error") {
    return <p className="mt-2 text-sm text-amber-800">Work-email provider temporarily unavailable.</p>;
  }
  if (person.email_status === "provider_unavailable") {
    return <p className="mt-2 text-sm text-amber-800">Work-email provider temporarily unavailable.</p>;
  }
  if (person.email_status === "rate_limited") {
    return <p className="mt-2 text-sm text-amber-800">The work-email provider rate limit has been reached.</p>;
  }
  if (person.email_status === "budget_exceeded") {
    return <p className="mt-2 text-sm text-[var(--text-muted)]">Work-email lookup is unavailable because its daily limit was reached.</p>;
  }
  if (person.email_status === "employment_conflict") {
    return <p className="mt-2 text-sm text-amber-800">Work email is unavailable until current employment is revalidated.</p>;
  }
  if (person.email_status === "identity_uncertain") {
    return <p className="mt-2 text-sm text-amber-800">Work email is unavailable because the professional identity is uncertain.</p>;
  }
  return null;
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
    guidance?: string
  ) => Promise<void>;
}) {
  if (!draft || !context) return null;
  const close = () => {
    setDraft(null);
    setContext(null);
  };
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="outreach-draft-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-xl rounded-xl bg-[var(--surface)] p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 id="outreach-draft-title" className="text-lg font-semibold">Review outreach draft</h3>
          <button aria-label="Close outreach draft" onClick={close}><X /></button>
        </div>
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
          <label className="mt-4 block text-sm">
            Subject
            <input
              aria-label="Outreach subject"
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] p-2"
              value={draft.subject}
              onChange={(event) => setDraft({ ...draft, subject: event.target.value })}
            />
          </label>
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
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <span className="text-xs text-[var(--text-muted)]">{draft.character_count} characters</span>
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
        </div>
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
