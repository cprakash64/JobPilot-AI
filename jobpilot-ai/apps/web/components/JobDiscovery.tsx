"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, Search, X } from "lucide-react";
import {
  api,
  ApiError,
  type DiscoverySummary,
  type GeneratedDocument,
  type Job,
  type JobsResponse,
  type RefreshSummary
} from "@/lib/api";
import { useJobsQuery, type JobsQuery } from "@/lib/jobsQuery";
import { Button } from "@/components/Button";
import { useAppShell } from "@/components/AppShell";
import { AutoApplyModal } from "@/components/AutoApplyModal";
import type { TrackerApplication, TrackerStatus } from "@/components/TrackerClient";
import { CompactJobCard, JobCard, type JobCardActions } from "@/components/jobs/JobCard";
import {
  DocumentGenerationModal,
  DocumentModal,
  type DocType
} from "@/components/jobs/documents";
import { JobDetailPanel, useEscapeToClose, type DetailTab } from "@/components/jobs/JobDetailPanel";
import { JobsFilterBar } from "@/components/jobs/JobsFilterBar";
import { dateValue, daysAgo } from "@/components/jobs/format";

/**
 * The Jobs workspace.
 *
 * One component owns the list, the selection, and the detail view, because they
 * are one screen: opening a job collapses the app sidebar and splits the
 * workspace into a compact list and a large detail panel, without leaving the
 * page or unmounting the list. The open job lives in the URL, so reload, Back,
 * Forward, and a shared link all resolve to the same state.
 */
export function JobDiscovery() {
  const { query, setQuery, selectJob, closeJob } = useJobsQuery();
  const shell = useAppShell();

  const [jobs, setJobs] = useState<Job[]>([]);
  const [listLoaded, setListLoaded] = useState(false);
  const [profileComplete, setProfileComplete] = useState(true);
  const [hasDiscovered, setHasDiscovered] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  // Which posted-within window the currently shown list came from. Derived
  // rather than flagged, so the spinner cannot get stuck out of sync with the
  // window the user selected.
  const [loadedWindow, setLoadedWindow] = useState<number | null>(null);

  const [applyJobId, setApplyJobId] = useState<number | null>(null);
  const [docModal, setDocModal] = useState<{ doc: GeneratedDocument; subtitle: string } | null>(null);
  const [documents, setDocuments] = useState<Record<number, Partial<Record<DocType, GeneratedDocument>>>>({});
  const [generating, setGenerating] = useState<{ jobId: number; type: DocType } | null>(null);
  const [tracker, setTracker] = useState<Record<number, TrackerStatus>>({});

  // The active tab is stored against the job it belongs to, so switching jobs
  // resolves to Overview during the *first* render of the new job rather than
  // in an effect afterwards. That ordering matters: a Networking tab left
  // mounted for even one commit would read the new job's people.
  const [tabState, setTabState] = useState<{ jobId: number | null; tab: DetailTab }>({
    jobId: null,
    tab: "overview"
  });
  const listScroll = useRef(0);
  const generatingRef = useRef(false);

  const selectedId = query.job;
  const detailOpen = selectedId !== null;

  // The app sidebar becomes a slim brand rail while a job is open, and returns
  // when the workspace closes. The user's own toggle still wins until then.
  useEffect(() => {
    shell?.requestCollapsed(detailOpen);
  }, [detailOpen, shell]);

  // Re-fetch from the server whenever the "Posted within" window changes so the
  // list actually reflects the selected range. The backend applies the date
  // filter — widening the window can surface jobs the server previously
  // withheld, which a client-only filter could never do.
  useEffect(() => {
    let active = true;
    const requestedWindow = query.postedWithin;
    void (async () => {
      try {
        const result = await api<JobsResponse>(`/jobs?posted_within_days=${requestedWindow}`);
        if (!active) return;
        setJobs(result.jobs);
        setProfileComplete(result.profile_complete);
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load jobs.");
        }
      } finally {
        if (active) {
          setLoadedWindow(requestedWindow);
          setListLoaded(true);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [query.postedWithin]);

  const dateRefreshing = loadedWindow !== query.postedWithin;

  // Saved/applied state comes from the user's own ledger, so a card can show it
  // without every card asking for it.
  const loadTracker = useCallback(async () => {
    try {
      const result = await api<{ applications?: TrackerApplication[] }>("/jobs/tracker/all");
      const next: Record<number, TrackerStatus> = {};
      for (const application of result.applications ?? []) {
        next[application.job_id] = application.status;
      }
      setTracker(next);
    } catch {
      // The list is fully usable without tracker decoration.
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await loadTracker();
    })();
  }, [loadTracker]);

  // Controlled polling: while any visible job is still being scored in the
  // background, re-fetch the list on a light interval so "Calculating fit…"
  // resolves into a real score without a manual refresh. Polling STOPS as soon
  // as no visible score is pending, so a fully-scored list never polls.
  const hasPendingScores = useMemo(
    () =>
      jobs.some((job) => {
        const state = job.match?.score_state ?? (job.match?.fit_score == null ? "pending" : "scored");
        return state === "pending" || state === "scoring";
      }),
    [jobs]
  );

  useEffect(() => {
    if (!hasPendingScores) {
      return;
    }
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const result = await api<JobsResponse>(`/jobs?posted_within_days=${query.postedWithin}`);
        if (active) {
          setJobs(result.jobs);
        }
      } catch {
        // Transient; the next tick (or a manual action) will retry.
      }
    }, 4000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [hasPendingScores, query.postedWithin, jobs]);

  const filtered = useMemo(() => {
    // Filtering "posted within N days" is inherently time-dependent.
    // eslint-disable-next-line react-hooks/purity
    const now = Date.now();
    return jobs
      .filter((job) => {
        const haystack = `${job.title} ${job.description_clean} ${job.required_skills.join(" ")}`.toLowerCase();
        const fit = job.match?.fit_score ?? 0;
        const withinDays = daysAgo(job.posted_at, now);
        return (
          (!query.q || haystack.includes(query.q.toLowerCase())) &&
          (!query.workplace || (job.workplace_type || "").toLowerCase() === query.workplace) &&
          fit >= query.minFit &&
          (withinDays === null || withinDays <= query.postedWithin)
        );
      })
      .sort((a, b) => {
        if (query.sort === "fit") {
          return (b.match?.fit_score ?? -1) - (a.match?.fit_score ?? -1);
        }
        const postedDifference = dateValue(b.posted_at) - dateValue(a.posted_at);
        return postedDifference || (b.match?.fit_score ?? -1) - (a.match?.fit_score ?? -1);
      });
  }, [jobs, query.q, query.workplace, query.minFit, query.postedWithin, query.sort]);

  const jobFromList = useMemo(
    () => (selectedId === null ? null : jobs.find((job) => job.id === selectedId) ?? null),
    [jobs, selectedId]
  );

  // A deep link (or a job filtered out of the current list) is fetched on its
  // own, once, after the list has had its chance to supply it. Both pieces of
  // state are tagged with the job they describe, so a stale response or an old
  // error can never be shown against the job now open.
  const [fetchedJob, setFetchedJob] = useState<Job | null>(null);
  const [detailAttempt, setDetailAttempt] = useState<{ jobId: number; error: string } | null>(null);

  const detailResolved =
    selectedId !== null && (fetchedJob?.id === selectedId || detailAttempt?.jobId === selectedId);
  const needsDetailFetch = selectedId !== null && !jobFromList && listLoaded && !detailResolved;

  useEffect(() => {
    if (!needsDetailFetch || selectedId === null) {
      return;
    }
    const controller = new AbortController();
    void (async () => {
      try {
        const result = await api<{ job: Job }>(`/jobs/${selectedId}`, { signal: controller.signal });
        setFetchedJob(result.job);
        setDetailAttempt({ jobId: selectedId, error: "" });
      } catch (loadError) {
        if (loadError instanceof ApiError && loadError.code === "request_cancelled") {
          return;
        }
        setDetailAttempt({
          jobId: selectedId,
          error:
            loadError instanceof ApiError && loadError.code === "not_found"
              ? "This job is no longer listed."
              : "This job could not be loaded."
        });
      }
    })();
    // Any in-flight detail request for a job the user has moved away from is
    // dropped, so a slow response can never overwrite the current job.
    return () => controller.abort();
  }, [needsDetailFetch, selectedId]);

  const selectedJob = jobFromList ?? (fetchedJob?.id === selectedId ? fetchedJob : null);
  const detailLoading = selectedId !== null && !selectedJob && (!listLoaded || needsDetailFetch);
  const detailError = detailAttempt?.jobId === selectedId ? detailAttempt.error : "";

  const tab: DetailTab = tabState.jobId === selectedId ? tabState.tab : "overview";
  const setTab = useCallback(
    (next: DetailTab) => setTabState({ jobId: selectedId, tab: next }),
    [selectedId]
  );

  const selectedIndex = useMemo(
    () => (selectedId === null ? -1 : filtered.findIndex((job) => job.id === selectedId)),
    [filtered, selectedId]
  );

  const openJob = useCallback(
    (jobId: number, targetTab?: DetailTab) => {
      if (typeof window !== "undefined") {
        listScroll.current = window.scrollY;
      }
      // Claiming the tab for the job being opened means the panel's first
      // render already shows the right section.
      setTabState({ jobId, tab: targetTab ?? "overview" });
      selectJob(jobId);
    },
    [selectJob]
  );

  // Returning to the list puts the user back where they were reading.
  useEffect(() => {
    if (detailOpen || typeof window === "undefined" || !listScroll.current) {
      return;
    }
    const offset = listScroll.current;
    listScroll.current = 0;
    window.scrollTo(0, offset);
  }, [detailOpen]);

  async function findFreshJobs() {
    setDiscovering(true);
    setError("");
    setMessage("Searching official/public job sources…");
    setWarnings([]);
    try {
      const result = await api<JobsResponse>("/jobs/discover", {
        method: "POST",
        body: JSON.stringify({ posted_within_days: query.postedWithin })
      });
      setJobs(result.jobs);
      setProfileComplete(result.profile_complete);
      setHasDiscovered(true);
      applyDiscoverySummary(result.discovery);
    } catch (discoverError) {
      setError(discoverError instanceof Error ? discoverError.message : "Job discovery failed.");
      setMessage("");
    } finally {
      setDiscovering(false);
    }
  }

  function applyDiscoverySummary(discovery?: DiscoverySummary) {
    setWarnings(summarizeSourceWarnings(discovery?.source_warnings ?? []));
    if (!discovery) {
      setMessage("");
      return;
    }
    const eligible = discovery.eligible ?? discovery.fresh;
    setMessage(`Showing ${eligible} job(s) matched to your profile.`);
  }

  async function refreshMatches() {
    setRefreshing(true);
    setError("");
    setMessage("Re-scoring jobs against your profile…");
    try {
      const result = await api<{ jobs: Job[]; summary: RefreshSummary; profile_complete: boolean }>(
        `/jobs/refresh-matches?posted_within_days=${query.postedWithin}`,
        { method: "POST" }
      );
      setJobs(result.jobs);
      setProfileComplete(result.profile_complete);
      setWarnings(summarizeSourceWarnings(result.summary.source_warnings ?? []));
      setMessage(`Re-scored ${result.summary.rescored_count} jobs · ${result.summary.matched_count} match your profile.`);
    } catch (refreshError) {
      if (process.env.NODE_ENV === "development") {
        console.error("refresh-matches failed", refreshError);
      }
      const raw = refreshError instanceof Error ? refreshError.message : "";
      // A readable backend message (e.g. the 422 profile message) is shown as-is;
      // opaque network errors become a friendly retry message.
      setError(raw && !/failed to fetch/i.test(raw) ? raw : "Could not refresh matches. Please try again.");
    } finally {
      setRefreshing(false);
    }
  }

  const jobById = useCallback(
    (jobId: number) => jobs.find((job) => job.id === jobId) ?? (fetchedJob?.id === jobId ? fetchedJob : null),
    [jobs, fetchedJob]
  );

  const generateDocument = useCallback(
    async (jobId: number, type: DocType) => {
      const job = jobById(jobId);
      // A ref, not the state, so a second click cannot slip through before the
      // re-render — and so the card callbacks stay referentially stable.
      if (!job || generatingRef.current) {
        return;
      }
      generatingRef.current = true;
      setGenerating({ jobId, type });
      setError("");
      try {
        const path = type === "resume" ? `/jobs/${jobId}/generate-resume` : `/jobs/${jobId}/generate-cover-letter`;
        const doc = await api<GeneratedDocument>(path, { method: "POST" });
        setDocuments((current) => ({ ...current, [jobId]: { ...current[jobId], [type]: doc } }));
        setDocModal({ doc, subtitle: `${job.title} · ${job.company}` });
      } catch (genError) {
        if (process.env.NODE_ENV === "development") {
          console.error("document generation failed", genError);
        }
        setError("Could not generate this document. Please check that your profile has enough information.");
      } finally {
        generatingRef.current = false;
        setGenerating(null);
      }
    },
    [jobById]
  );

  const saveJob = useCallback(async (jobId: number) => {
    try {
      const result = await api<{ tracker: { status: TrackerStatus } }>(`/jobs/${jobId}/save`, { method: "POST" });
      setTracker((current) => ({ ...current, [jobId]: result.tracker?.status ?? "saved" }));
      setMessage("Job saved to your tracker.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save job.");
    }
  }, []);

  const cardActions = useMemo<JobCardActions>(
    () => ({
      onSelect: (jobId) => openJob(jobId),
      onSave: (jobId) => void saveJob(jobId),
      onApply: (jobId) => setApplyJobId(jobId),
      onGenerate: (jobId, type) => void generateDocument(jobId, type),
      onOpenPeople: (jobId) => openJob(jobId, "networking")
    }),
    [openJob, saveJob, generateDocument]
  );

  const applyJob = applyJobId === null ? null : jobById(applyJobId);
  const modalOpen = Boolean(applyJob || docModal || generating);
  useEscapeToClose(detailOpen && !modalOpen, closeJob);

  const busy = discovering || refreshing;
  const onFilterChange = useCallback((patch: Partial<JobsQuery>) => setQuery(patch, "replace"), [setQuery]);

  const resultSummary = `${filtered.length} job${filtered.length === 1 ? "" : "s"} for you`;

  const modals = (
    <>
      {generating && (
        <DocumentGenerationModal type={generating.type} job={jobById(generating.jobId)} />
      )}
      {docModal && <DocumentModal doc={docModal.doc} subtitle={docModal.subtitle} onClose={() => setDocModal(null)} />}
      {applyJob && (
        <AutoApplyModal
          jobId={applyJob.id}
          jobTitle={applyJob.title}
          company={applyJob.company}
          officialUrl={applyJob.application_url}
          onClose={() => {
            setApplyJobId(null);
            void loadTracker();
          }}
        />
      )}
    </>
  );

  if (detailOpen) {
    return (
      // Exactly two scroll regions live below: the compact list and the detail
      // panel. This container is pinned to the viewport and never scrolls, and
      // AppShell pins the document behind it.
      <div className="flex h-full min-h-0 flex-col overflow-hidden">
        <p role="status" aria-live="polite" className="sr-only">
          {selectedJob ? `Showing ${selectedJob.title} at ${selectedJob.company}` : "Loading job details"}
        </p>
        <div className="flex min-h-0 flex-1">
          <aside
            aria-label="Job results"
            className="hidden w-[304px] shrink-0 flex-col border-r border-line bg-[var(--background)] md:flex lg:w-[340px]"
          >
            <div className="shrink-0 px-3 pb-2.5 pt-3">
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[var(--text-muted)]" />
                  <input
                    aria-label="Role or skill"
                    className="h-9 w-full rounded-lg border border-line bg-panel/40 pl-9 pr-3 text-sm"
                    placeholder="Search role or skill"
                    value={query.q}
                    onChange={(event) => onFilterChange({ q: event.target.value })}
                  />
                </div>
                <button
                  type="button"
                  onClick={closeJob}
                  aria-label="Show all jobs"
                  title="Show all jobs"
                  className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-lg text-[var(--text-muted)] transition-colors hover:bg-panel hover:text-[var(--text-secondary)]"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <p className="mt-2.5 px-0.5 text-xs text-[var(--text-muted)]">{resultSummary}</p>
            </div>
            <CompactJobList
              jobs={filtered}
              selectedId={selectedId}
              tracker={tracker}
              onSelect={(jobId) => openJob(jobId)}
            />
          </aside>

          <section className="min-w-0 flex-1 bg-[var(--background)]">
            <JobDetailPanel
              key={selectedId}
              job={selectedJob}
              loading={detailLoading}
              error={detailError || error}
              tab={tab}
              onTabChange={setTab}
              trackerStatus={selectedJob ? tracker[selectedJob.id] ?? null : null}
              generating={generating?.jobId === selectedId ? generating.type : null}
              documents={selectedId !== null ? documents[selectedId] ?? {} : {}}
              onClose={closeJob}
              onSave={() => selectedJob && void saveJob(selectedJob.id)}
              onApply={() => selectedJob && setApplyJobId(selectedJob.id)}
              onGenerate={(type) => selectedJob && void generateDocument(selectedJob.id, type)}
              onPreviewDocument={(doc) =>
                selectedJob && setDocModal({ doc, subtitle: `${selectedJob.title} · ${selectedJob.company}` })
              }
              onPrevious={
                selectedIndex > 0 ? () => openJob(filtered[selectedIndex - 1].id) : null
              }
              onNext={
                selectedIndex >= 0 && selectedIndex < filtered.length - 1
                  ? () => openJob(filtered[selectedIndex + 1].id)
                  : null
              }
              position={selectedIndex >= 0 ? `${selectedIndex + 1} of ${filtered.length}` : null}
            />
          </section>
        </div>
        {modals}
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-[1080px] px-5 pb-16 pt-8 sm:px-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[1.75rem] font-semibold tracking-[-0.02em]">Jobs</h1>
          <p className="mt-1.5 text-[15px] text-[var(--text-muted)]">
            Fresh roles matched to your profile, from official application sources.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={findFreshJobs} disabled={busy}>
            {discovering ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />} Find fresh jobs
          </Button>
          <Button variant="secondary" type="button" onClick={refreshMatches} disabled={busy}>
            {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Refresh matches
          </Button>
        </div>
      </header>

      {(message || error) && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {message && <p className="text-sm text-[var(--text-muted)]">{message}</p>}
          {error && <p className="text-sm text-[var(--danger)]">{error}</p>}
        </div>
      )}

      {!profileComplete && (
        <div className="mt-5 rounded-xl border border-[var(--warning-border)] bg-[var(--warning-surface)] p-4 text-sm text-[var(--warning)]">
          <p className="font-semibold">Complete your profile to discover better jobs.</p>
          <p className="mt-1 leading-6">
            Add target roles and skills on your <a className="font-medium text-pine underline" href="/profile">profile</a> so we can
            match jobs to you. You can still run a basic search with what you have.
          </p>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="mt-5 rounded-xl border border-[var(--warning-border)] bg-[var(--warning-surface)] p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--warning)]" />
            <div className="text-sm text-[var(--warning)]">
              <p className="font-semibold">A small number of sources are temporarily unavailable</p>
              <ul className="mt-1 list-disc pl-5 leading-6">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      <div className="mt-6">
        <JobsFilterBar query={query} onChange={onFilterChange} dateRefreshing={dateRefreshing} />
      </div>

      <div className="mt-5 flex flex-wrap items-baseline justify-between gap-2 text-sm text-[var(--text-muted)]">
        <p>
          <span className="font-medium text-[var(--text-secondary)]">{resultSummary}</span>
          {` · Posted in the last ${query.postedWithin === 1 ? "24 hours" : `${query.postedWithin} days`}`}
        </p>
        {hasDiscovered && filtered.length > 0 && filtered.length < 10 && (
          <p className="text-[var(--text-muted)]">
            Only a few strict matches — add target roles or locations to broaden results.
          </p>
        )}
      </div>

      <div className="mt-3.5 grid gap-3">
        {filtered.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            generating={generating?.jobId === job.id ? generating.type : null}
            trackerStatus={tracker[job.id] ?? null}
            actions={cardActions}
          />
        ))}
        {filtered.length === 0 && (
          <div className="rounded-2xl border border-line bg-white px-6 py-14 text-center">
            <p className="mx-auto max-w-sm text-sm leading-6 text-[var(--text-muted)]">
              {!profileComplete
                ? "Complete your profile to discover better jobs."
                : hasDiscovered
                  ? "No fresh jobs matched your selected roles, level, and locations. Try broadening your target roles or locations."
                  : "Click “Find fresh jobs” to pull recent postings matched to your profile."}
            </p>
          </div>
        )}
      </div>
      {modals}
    </div>
  );
}

function CompactJobList({
  jobs,
  selectedId,
  tracker,
  onSelect
}: {
  jobs: Job[];
  selectedId: number | null;
  tracker: Record<number, TrackerStatus>;
  onSelect: (jobId: number) => void;
}) {
  const container = useRef<HTMLUListElement>(null);

  // Keep the open job visible when the selection moves from the detail panel
  // (previous/next) rather than from a click in this list.
  useEffect(() => {
    const selected = container.current?.querySelector('[data-selected="true"]');
    if (selected instanceof HTMLElement && typeof selected.scrollIntoView === "function") {
      selected.scrollIntoView({ block: "nearest" });
    }
  }, [selectedId]);

  return (
    <ul
      ref={container}
      className="min-h-0 flex-1 space-y-1 overflow-y-auto overflow-x-hidden overscroll-contain px-2 pb-3"
    >
      {jobs.map((job) => (
        <CompactJobCard
          key={job.id}
          job={job}
          selected={job.id === selectedId}
          trackerStatus={tracker[job.id] ?? null}
          onSelect={onSelect}
        />
      ))}
      {jobs.length === 0 && (
        <li className="px-3 py-6 text-sm text-[var(--text-muted)]">No jobs match the current filters.</li>
      )}
    </ul>
  );
}

function summarizeSourceWarnings(warnings: string[]): string[] {
  const timeouts = warnings.filter((warning) => /TimeoutError|timed?\s*out/i.test(warning));
  const other = warnings.filter((warning) => !/TimeoutError|timed?\s*out/i.test(warning));
  const summary = timeouts.length > 0
    ? [`${timeouts.length} source${timeouts.length === 1 ? "" : "s"} responded too slowly. Jobs from all other sources are shown, and the daily refresh will retry automatically.`]
    : [];
  return [...summary, ...other.slice(0, 5)];
}
