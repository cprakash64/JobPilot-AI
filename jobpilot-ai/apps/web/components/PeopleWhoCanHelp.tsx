"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ExternalLink, Mail, MessageSquareText, Star, UserCheck, Users, X } from "lucide-react";
import { api, ApiError, type PeopleRecommendation, type PeopleResponse } from "@/lib/api";
import {
  discoverPeople,
  getCachedPeople,
  loadPeople,
  subscribeToPeople
} from "@/lib/peopleClient";
import { Button } from "@/components/Button";

type JobId = string | number;
type PersonAction = "email" | "save" | "unsave" | "contacted" | "incorrect";

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
  return messages[data.availability_reason ?? ""] ??
    "The professional data provider is temporarily unavailable. You can safely retry later.";
}

function confidenceText(value: PeopleRecommendation["confidence"]) {
  return `${value[0].toUpperCase()}${value.slice(1)} confidence`;
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
  const [loading, setLoading] = useState(loadOnMount);
  const [discovering, setDiscovering] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const discoveryInFlight = useRef(false);

  const load = useCallback(async (force = true) => {
    setLoading(true);
    setError("");
    try {
      const response = await loadPeople(jobId, force);
      setData(response);
      return response;
    } catch (loadError) {
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

  const personAction = useCallback(async (person: PeopleRecommendation, action: PersonAction) => {
    setActionId(person.recommendation_id);
    setError("");
    try {
      if (action === "incorrect") {
        await api(`/jobs/${jobId}/people/${person.recommendation_id}/feedback`, {
          method: "POST",
          body: JSON.stringify({
            information_correct_rating: "incorrect",
            incorrect_reason: "Reported from the job-details card."
          })
        });
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

  const draftOutreach = useCallback(async (person: PeopleRecommendation) => {
    setActionId(person.recommendation_id);
    setError("");
    try {
      const type =
        person.category === "potential_hiring_manager"
          ? "potential_hiring_manager_introduction"
          : person.category === "potential_referrer"
            ? "referral_request"
            : "recruiter_introduction";
      const response = await api<{ draft: string }>(
        `/jobs/${jobId}/people/${person.recommendation_id}/outreach-draft`,
        { method: "POST", body: JSON.stringify({ draft_type: type }) }
      );
      setDraft(response.draft);
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
    loading,
    discovering,
    actionId,
    draft,
    setDraft,
    load,
    discover,
    personAction,
    draftOutreach
  };
}

export function PeopleWhoCanHelp({ jobId }: { jobId: JobId }) {
  const titleId = useId();
  const controller = usePeopleController(jobId, true);
  const { data, error, loading, discovering } = controller;
  const availableMessage = data ? availabilityMessage(data) : null;
  const canDiscover =
    data?.availability_reason !== "not_in_rollout" &&
    data?.status !== "disabled" &&
    (
      data?.status === "not_started" ||
      data?.status === "no_reliable_matches" ||
      data?.status === "stale" ||
      data?.status === "provider_unavailable"
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
        {canDiscover ? (
          <Button onClick={() => void controller.discover()} disabled={discovering}>
            {discovering || data?.status === "stale" ? "Refresh people" : "Find people"}
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
            <Button variant="secondary" className="mt-3" onClick={() => void controller.load(true)}>
              Retry
            </Button>
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
          <p className="text-sm text-[var(--text-muted)]">
            No sufficiently reliable matches were found. JobPilot will not fill categories with weak results.
          </p>
        ) : null}
        {data?.status === "provider_unavailable" ? (
          <p className="text-sm text-[var(--text-muted)]">
            {providerFailureMessage(data)}
          </p>
        ) : null}
      </div>

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
                    No sufficiently reliable matches in this category.
                  </p>
                )}
              </section>
            );
          })}
        </div>
      ) : null}

      <OutreachDraft draft={controller.draft} setDraft={controller.setDraft} />
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
  const { data, error, loading, discovering } = controller;
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
              <Button variant="secondary" className="mt-3" onClick={() => void loadAndMaybeDiscover(true)}>
                Retry
              </Button>
            </div>
          ) : null}
          {data?.status === "provider_unavailable" ? (
            <StateWithRetry
              text={providerFailureMessage(data)}
              onRetry={() => void controller.discover()}
            />
          ) : null}
          {data?.status === "no_reliable_matches" ? (
            <StateWithRetry
              text="No sufficiently reliable matches were found."
              onRetry={() => void controller.discover()}
            />
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
        {emailEnabled && ["not_requested", "provider_error"].includes(person.email_status) ? (
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
  onDraft: (person: PeopleRecommendation) => Promise<void>;
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
        {emailEnabled && ["not_requested", "provider_error"].includes(person.email_status) ? (
          <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm" disabled={busy} onClick={() => void onAction(person, "email")}>
            <Mail className="mr-1 inline h-4 w-4" />
            {person.email_status === "provider_error" ? "Retry work email" : "Find work email"}
          </button>
        ) : null}
        {outreachEnabled ? (
          <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm" disabled={busy} onClick={() => void onDraft(person)}>
            <MessageSquareText className="mr-1 inline h-4 w-4" /> Draft outreach
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
  return null;
}

function OutreachDraft({
  draft,
  setDraft
}: {
  draft: string;
  setDraft: (value: string) => void;
}) {
  if (!draft) return null;
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="outreach-draft-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-xl rounded-xl bg-[var(--surface)] p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 id="outreach-draft-title" className="text-lg font-semibold">Review outreach draft</h3>
          <button aria-label="Close outreach draft" onClick={() => setDraft("")}><X /></button>
        </div>
        <textarea
          aria-label="Outreach draft"
          className="mt-4 min-h-56 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] p-3"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
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
