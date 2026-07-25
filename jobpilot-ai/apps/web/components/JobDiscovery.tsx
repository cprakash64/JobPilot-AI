"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Banknote,
  Briefcase,
  Building2,
  CalendarDays,
  Check,
  ExternalLink,
  FileText,
  Loader2,
  Mail,
  MapPin,
  RefreshCw,
  Save,
  Search,
  SlidersHorizontal,
  X
} from "lucide-react";
import {
  api,
  API_URL,
  type CoverLetterContent,
  type DiscoverySummary,
  type GeneratedDocument,
  type Job,
  type JobsResponse,
  type RefreshSummary,
  type ResumeContent,
  type ScoreState
} from "@/lib/api";
import { getFitScoreTone, getScoreDisplay } from "@/lib/fitScore";
import { CompanyLogo } from "@/components/CompanyLogo";
import { Button } from "@/components/Button";
import { GeneratedResumePreview } from "@/components/GeneratedResumePreview";
import { GeneratedCoverLetterPreview } from "@/components/GeneratedCoverLetterPreview";
import { AutoApplyModal } from "@/components/AutoApplyModal";

type DocType = "resume" | "cover_letter";
type JobSort = "newest" | "fit";

const WORKPLACE_OPTIONS = ["", "remote", "hybrid", "onsite"] as const;

export function JobDiscovery() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [profileComplete, setProfileComplete] = useState(true);
  const [hasDiscovered, setHasDiscovered] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [detailsId, setDetailsId] = useState<number | null>(null);
  const [applyJob, setApplyJob] = useState<Job | null>(null);
  const [docModal, setDocModal] = useState<{ doc: GeneratedDocument; subtitle: string } | null>(null);
  const [generating, setGenerating] = useState<{ jobId: number; type: DocType } | null>(null);
  const [dateRefreshing, setDateRefreshing] = useState(true);

  const [role, setRole] = useState("");
  const [workplace, setWorkplace] = useState("");
  const [minFit, setMinFit] = useState(0);
  const [postedWithin, setPostedWithin] = useState(7);
  const [sortBy, setSortBy] = useState<JobSort>("newest");

  // Re-fetch from the server whenever the "Posted within" window changes so the
  // list actually reflects the selected range (7 vs 15 vs 30 days). The backend
  // applies the date filter — widening the window can surface jobs the server
  // previously withheld, which a client-only filter could never do.
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const result = await api<JobsResponse>(`/jobs?posted_within_days=${postedWithin}`);
        if (!active) return;
        setJobs(result.jobs);
        setProfileComplete(result.profile_complete);
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load jobs.");
        }
      } finally {
        if (active) {
          setDateRefreshing(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [postedWithin]);

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
        const result = await api<JobsResponse>(`/jobs?posted_within_days=${postedWithin}`);
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
  }, [hasPendingScores, postedWithin, jobs]);

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
          (!role || haystack.includes(role.toLowerCase())) &&
          (!workplace || (job.workplace_type || "").toLowerCase() === workplace) &&
          fit >= minFit &&
          (withinDays === null || withinDays <= postedWithin)
        );
      })
      .sort((a, b) => {
        if (sortBy === "fit") {
          return (b.match?.fit_score ?? -1) - (a.match?.fit_score ?? -1);
        }
        const postedDifference = dateValue(b.posted_at) - dateValue(a.posted_at);
        return postedDifference || (b.match?.fit_score ?? -1) - (a.match?.fit_score ?? -1);
      });
  }, [jobs, role, workplace, minFit, postedWithin, sortBy]);

  const detailsJob = useMemo(() => jobs.find((job) => job.id === detailsId) ?? null, [jobs, detailsId]);

  async function findFreshJobs() {
    setDiscovering(true);
    setError("");
    setMessage("Searching official/public job sources…");
    setWarnings([]);
    try {
      const result = await api<JobsResponse>("/jobs/discover", {
        method: "POST",
        body: JSON.stringify({ posted_within_days: postedWithin })
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
        `/jobs/refresh-matches?posted_within_days=${postedWithin}`,
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

  async function generateDocument(job: Job, type: DocType) {
    setGenerating({ jobId: job.id, type });
    setError("");
    try {
      const path = type === "resume" ? `/jobs/${job.id}/generate-resume` : `/jobs/${job.id}/generate-cover-letter`;
      const doc = await api<GeneratedDocument>(path, { method: "POST" });
      setDocModal({ doc, subtitle: `${job.title} · ${job.company}` });
    } catch (genError) {
      if (process.env.NODE_ENV === "development") {
        console.error("document generation failed", genError);
      }
      setError("Could not generate this document. Please check that your profile has enough information.");
    } finally {
      setGenerating(null);
    }
  }

  async function saveJob(jobId: number) {
    try {
      await api(`/jobs/${jobId}/save`, { method: "POST" });
      setMessage("Job saved to your tracker.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save job.");
    }
  }

  const busy = discovering || refreshing;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={findFreshJobs} disabled={busy}>
          {discovering ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />} Find fresh jobs
        </Button>
        <Button variant="secondary" type="button" onClick={refreshMatches} disabled={busy}>
          {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Refresh matches
        </Button>
        {message && <p className="self-center text-sm text-[var(--text-muted)]">{message}</p>}
        {error && <p className="self-center text-sm text-[var(--danger)]">{error}</p>}
      </div>

      {!profileComplete && (
        <div className="mb-4 rounded-lg border border-[var(--warning-border)] bg-[var(--warning-surface)] p-4 text-sm text-[var(--warning)]">
          <p className="font-semibold">Complete your profile to discover better jobs.</p>
          <p className="mt-1">
            Add target roles and skills on your <a className="font-medium text-pine underline" href="/profile">profile</a> so we can
            match jobs to you. You can still run a basic search with what you have.
          </p>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="mb-4 rounded-lg border border-[var(--warning-border)] bg-[var(--warning-surface)] p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--warning)]" />
            <div className="text-sm text-[var(--warning)]">
              <p className="font-semibold">A small number of sources are temporarily unavailable</p>
              <ul className="mt-1 list-disc pl-5">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      <div className="mb-4 rounded-2xl border border-line bg-white p-4 shadow-sm">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="rounded-xl bg-[var(--success-surface)] p-2 text-pine">
              <SlidersHorizontal className="h-4 w-4" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-ink">Refine your matches</h2>
              <p className="text-xs text-[var(--text-muted)]">
                Search by role, then narrow by fit, workplace, or recency.
              </p>
            </div>
          </div>
          {(role || workplace || minFit > 0) && (
            <button
              type="button"
              onClick={() => {
                setRole("");
                setWorkplace("");
                setMinFit(0);
              }}
              className="focus-ring rounded-lg px-3 py-2 text-xs font-medium text-[var(--text-muted)] hover:bg-panel"
            >
              Clear filters
            </button>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
          <label className="xl:col-span-2">
            <span className="mb-1.5 block text-xs font-medium text-[var(--text-muted)]">
              Role or skill
            </span>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-[var(--text-muted)]" />
              <input
                aria-label="Role or skill"
                className="h-11 w-full rounded-lg border border-line bg-panel/20 pl-9 pr-3 text-sm"
                placeholder="e.g. Software Engineer"
                value={role}
                onChange={(event) => setRole(event.target.value)}
              />
            </div>
          </label>

          <FilterSelect
            label="Workplace"
            ariaLabel="Workplace type"
            value={workplace}
            onChange={setWorkplace}
            options={WORKPLACE_OPTIONS.map((option) => ({
              value: option,
              label: option ? option[0].toUpperCase() + option.slice(1) : "Any workplace"
            }))}
          />

          <FilterSelect
            label="Minimum fit"
            ariaLabel="Minimum fit score"
            value={String(minFit)}
            onChange={(value) => setMinFit(Number(value))}
            options={[
              { value: "0", label: "Any fit" },
              { value: "60", label: "60% and above" },
              { value: "70", label: "70% and above" },
              { value: "80", label: "80% and above" }
            ]}
          />

          <div className="relative">
            <FilterSelect
              label="Posted within"
              ariaLabel="Posted within days"
              value={String(postedWithin)}
              onChange={(value) => {
                setDateRefreshing(true);
                setPostedWithin(Number(value));
              }}
              options={[
                { value: "1", label: "Past 24 hours" },
                { value: "3", label: "Past 3 days" },
                { value: "7", label: "Past 7 days" },
                { value: "14", label: "Past 14 days" },
                { value: "30", label: "Past 30 days" }
              ]}
            />
            {dateRefreshing && (
              <Loader2
                aria-label="Refreshing date range"
                className="absolute right-8 top-8 h-4 w-4 animate-spin text-pine"
              />
            )}
          </div>

          <FilterSelect
            label="Sort by"
            ariaLabel="Sort jobs"
            value={sortBy}
            onChange={(value) => setSortBy(value as JobSort)}
            options={[
              { value: "newest", label: "Newest first" },
              { value: "fit", label: "Best fit" }
            ]}
          />
        </div>
      </div>

      <div className="mb-3 text-sm text-[var(--text-muted)]">
        <p>
          <span className="font-medium text-[var(--text-secondary)]">
            {filtered.length} job{filtered.length === 1 ? "" : "s"} for you
          </span>
          {` · Posted in the last ${postedWithin === 1 ? "24 hours" : `${postedWithin} days`}`}
        </p>
      </div>

      {hasDiscovered && filtered.length > 0 && filtered.length < 10 && (
        <p className="mb-3 rounded-md border border-[var(--warning-border)] bg-[var(--warning-surface)] px-3 py-2 text-sm text-[var(--warning)]">
          We found only a few strict matches. Add more target roles or locations to broaden results.
        </p>
      )}

      <div className="grid gap-4">
        {filtered.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            generating={generating?.jobId === job.id ? generating.type : null}
            onSave={() => saveJob(job.id)}
            onApply={() => setApplyJob(job)}
            onDetails={() => setDetailsId(job.id)}
            onResume={() => generateDocument(job, "resume")}
            onCoverLetter={() => generateDocument(job, "cover_letter")}
          />
        ))}
        {filtered.length === 0 && (
          <div className="rounded-lg border border-line bg-white p-8 text-center text-[var(--text-muted)]">
            {!profileComplete
              ? "Complete your profile to discover better jobs."
              : hasDiscovered
                ? "No fresh jobs matched your selected roles, level, and locations. Try broadening your target roles or locations."
                : "Click “Find fresh jobs” to pull recent postings matched to your profile."}
          </div>
        )}
      </div>

      {detailsJob && (
        <JobDetailsModal
          job={detailsJob}
          onClose={() => setDetailsId(null)}
          onSave={() => saveJob(detailsJob.id)}
          onApply={() => {
            setApplyJob(detailsJob);
            setDetailsId(null);
          }}
        />
      )}
      {generating && (
        <DocumentGenerationModal
          type={generating.type}
          job={jobs.find((job) => job.id === generating.jobId) ?? null}
        />
      )}
      {docModal && <DocumentModal doc={docModal.doc} subtitle={docModal.subtitle} onClose={() => setDocModal(null)} />}
      {applyJob && (
        <AutoApplyModal
          jobId={applyJob.id}
          jobTitle={applyJob.title}
          company={applyJob.company}
          officialUrl={applyJob.application_url}
          onClose={() => setApplyJob(null)}
        />
      )}
    </div>
  );
}

const GENERATION_STEPS: Record<DocType, string[]> = {
  resume: [
    "Reading the role requirements",
    "Selecting your most relevant experience",
    "Tailoring language and keywords",
    "Checking accuracy and ATS structure"
  ],
  cover_letter: [
    "Understanding the role and company",
    "Choosing evidence from your profile",
    "Writing a focused first draft",
    "Checking accuracy and tone"
  ]
};

function DocumentGenerationModal({ type, job }: { type: DocType; job: Job | null }) {
  const [activeStep, setActiveStep] = useState(0);
  const steps = GENERATION_STEPS[type];
  const title = type === "resume" ? "Tailoring your resume" : "Writing your cover letter";

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveStep((current) => Math.min(current + 1, steps.length - 1));
    }, 2400);
    return () => window.clearInterval(timer);
  }, [type, steps.length]);

  return (
    <div className="assisted-application-backdrop fixed inset-0 z-50 p-4 sm:p-6">
      <div
        className="assisted-application-dialog w-full max-w-[540px] overflow-hidden rounded-[24px]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="document-generation-title"
      >
        <div className="border-b border-line px-6 py-5 sm:px-7">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-pine">Preparing your application</p>
          <h3 id="document-generation-title" className="mt-1.5 text-2xl font-semibold tracking-[-0.03em]">
            {title}
          </h3>
          {job && (
            <p className="mt-1.5 text-sm text-[var(--text-muted)]">
              {job.title} · {job.company}
            </p>
          )}
        </div>

        <div className="px-6 py-6 sm:px-7">
          <div className="h-1.5 overflow-hidden rounded-full bg-panel" aria-hidden="true">
            <div
              className="h-full rounded-full bg-pine transition-[width] duration-700 ease-out"
              style={{ width: `${Math.min(88, 18 + activeStep * 22)}%` }}
            />
          </div>
          <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">
            We’re using only facts from your saved profile and the employer’s job description.
          </p>

          <ol className="mt-5 grid gap-2" aria-live="polite">
            {steps.map((step, index) => {
              const complete = index < activeStep;
              const active = index === activeStep;
              return (
                <li
                  key={step}
                  className={`flex items-center gap-3 rounded-xl border px-3.5 py-3 text-sm transition ${
                    active
                      ? "border-[var(--success-border)] bg-[var(--success-surface)] text-[var(--text-primary)]"
                      : "border-transparent text-[var(--text-muted)]"
                  }`}
                >
                  <span
                    className={`grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                      complete ? "bg-pine text-white" : active ? "bg-white text-pine" : "border border-line"
                    }`}
                  >
                    {complete ? <Check className="h-3.5 w-3.5" /> : active ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                  </span>
                  <span className={active ? "font-medium" : ""}>{step}</span>
                </li>
              );
            })}
          </ol>

          <p className="mt-5 text-xs text-[var(--text-muted)]">
            Usually ready in under a minute. You’ll be able to preview, edit, and download it before applying.
          </p>
        </div>
      </div>
    </div>
  );
}

function FilterSelect({
  label,
  ariaLabel,
  value,
  onChange,
  options
}: {
  label: string;
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label>
      <span className="mb-1.5 block text-xs font-medium text-[var(--text-muted)]">{label}</span>
      <select
        aria-label={ariaLabel}
        className="h-11 w-full rounded-lg border border-line bg-white px-3 text-sm font-medium text-[var(--text-secondary)]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value || "any"} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
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

function JobCard({
  job,
  generating,
  onSave,
  onApply,
  onDetails,
  onResume,
  onCoverLetter
}: {
  job: Job;
  generating: DocType | null;
  onSave: () => void;
  onApply: () => void;
  onDetails: () => void;
  onResume: () => void;
  onCoverLetter: () => void;
}) {
  const fit = job.match?.fit_score ?? null;
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const reasons = job.match?.match_reasons?.slice(0, 2) ?? [];
  return (
    <article className="group rounded-2xl border border-line bg-white p-5 shadow-sm transition-shadow hover:shadow-md sm:p-6">
      {/* Top row: company identity + fit badge */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <CompanyLogo
            company={job.company}
            logoUrl={job.company_logo_url}
            proxyPath={job.company_logo_proxy_path}
            companyDomain={job.company_domain}
          />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-[var(--text-secondary)]">{job.company}</p>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <SourceBadge source={job.source} />
            </div>
          </div>
        </div>
        <FitBadge score={fit} label={job.match?.fit_label ?? null} scoreState={job.match?.score_state ?? null} />
      </div>

      {/* Title */}
      <h2 className="mt-3 text-xl font-semibold leading-snug tracking-tight text-ink sm:text-[1.35rem]">
        {job.title}
      </h2>

      {/* Metadata row */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-[var(--text-muted)]">
        {job.location && <Meta icon={<MapPin className="h-3.5 w-3.5" />}>{job.location}</Meta>}
        {job.workplace_type && job.workplace_type !== "unknown" && (
          <Meta icon={<Building2 className="h-3.5 w-3.5" />}>{capitalize(job.workplace_type)}</Meta>
        )}
        {salary && <Meta icon={<Banknote className="h-3.5 w-3.5" />}>{salary}</Meta>}
        {job.employment_type && (
          <Meta icon={<Briefcase className="h-3.5 w-3.5" />}>{formatEmployment(job.employment_type)}</Meta>
        )}
        {postedLabel(job.posted_at) && (
          <Meta icon={<CalendarDays className="h-3.5 w-3.5" />}>{postedLabel(job.posted_at)}</Meta>
        )}
      </div>

      {/* AI match explanation */}
      {reasons.length > 0 ? (
        <ul className="mt-3 grid gap-1 text-sm leading-6 text-[var(--text-secondary)]">
          {reasons.map((reason) => (
            <li key={reason} className="flex gap-2">
              <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-pine/70" />
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      ) : (
        (() => {
          const display = getScoreDisplay(job.match?.score_state ?? null, job.match?.fit_score ?? null);
          if (display.kind === "score") {
            return null;
          }
          return (
            <p className="mt-3 flex items-center gap-2 text-sm text-[var(--text-muted)]">
              {display.kind === "calculating" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              <span>{display.helper}</span>
              {display.kind === "profile_incomplete" && (
                <a href="/profile" className="font-medium text-pine underline">
                  Complete profile
                </a>
              )}
            </p>
          );
        })()
      )}

      {/* Gaps / risks — present but visually quiet */}
      {job.match && (job.match.missing_skills.length > 0 || job.match.risk_factors.length > 0) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-muted)]">
          {job.match.missing_skills.length > 0 && (
            <span>
              <span className="font-medium">Missing:</span> {job.match.missing_skills.slice(0, 4).join(", ")}
            </span>
          )}
          {job.match.risk_factors.length > 0 && (
            <span className="text-[var(--danger)]">{job.match.risk_factors[0]}</span>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line/70 pt-4">
        <AssistedApplyButton url={job.application_url} onApply={onApply} />
        <Button variant="secondary" type="button" onClick={onSave}><Save className="h-4 w-4" /> Save</Button>
        <Button variant="secondary" type="button" onClick={onDetails}>View details</Button>
        <Button variant="secondary" type="button" disabled={generating !== null} onClick={onResume}>
          {generating === "resume" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
          {generating === "resume" ? "Generating resume…" : "Resume"}
        </Button>
        <Button variant="secondary" type="button" disabled={generating !== null} onClick={onCoverLetter}>
          {generating === "cover_letter" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mail className="h-4 w-4" />}
          {generating === "cover_letter" ? "Generating cover letter…" : "Cover Letter"}
        </Button>
      </div>
    </article>
  );
}

function Meta({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[var(--text-muted)]">{icon}</span>
      {children}
    </span>
  );
}

function capitalize(value: string): string {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

function formatEmployment(value: string): string {
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map(capitalize)
    .join(" ");
}

function formatSalary(min?: number | null, max?: number | null, currency?: string | null): string | null {
  if (!min && !max) {
    return null;
  }
  const symbol = currency === "USD" || !currency ? "$" : `${currency} `;
  const fmt = (value: number) => (value >= 1000 ? `${Math.round(value / 1000)}k` : `${Math.round(value)}`);
  if (min && max) {
    return `${symbol}${fmt(min)}–${fmt(max)}`;
  }
  return `${symbol}${fmt((min || max)!)}`;
}

function DocumentModal({ doc, subtitle, onClose }: { doc: GeneratedDocument; subtitle: string; onClose: () => void }) {
  const isResume = doc.document_type === "resume";
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [resume, setResume] = useState<ResumeContent>(() => normalizeResume(doc.content));
  const [cover, setCover] = useState<CoverLetterContent>(() => normalizeCover(doc.content));
  const [status, setStatus] = useState("");

  const plainText = isResume ? resumeToPlainText(resume) : coverToPlainText(cover);
  const quality = doc.quality ?? {};
  const warnings = quality.warnings ?? doc.warnings ?? [];

  async function persist(): Promise<boolean> {
    const content = isResume ? resume : cover;
    await api(`/jobs/documents/${doc.document_id}`, {
      method: "PUT",
      body: JSON.stringify({ content, plain_text: plainText })
    });
    return true;
  }

  async function save() {
    setStatus("Saving…");
    try {
      await persist();
      setStatus("Saved.");
    } catch {
      setStatus("Could not save. Please try again.");
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(plainText);
      setStatus("Copied clean text to clipboard.");
    } catch {
      setStatus("Copy failed — select and copy manually.");
    }
  }

  async function download(fmt: "docx" | "pdf") {
    setStatus(`Preparing ${fmt.toUpperCase()}…`);
    try {
      await persist(); // ensure the export matches the (possibly edited) preview
      const token = typeof window !== "undefined" ? window.localStorage.getItem("jobpilot_token") : null;
      const response = await fetch(`${API_URL}/jobs/documents/${doc.document_id}/download/${fmt}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {}
      });
      if (!response.ok) {
        throw new Error("download failed");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${doc.title.replace(/\s+/g, "_")}.${fmt}`;
      link.click();
      URL.revokeObjectURL(url);
      setStatus("");
    } catch {
      setStatus(`Could not download ${fmt.toUpperCase()}.`);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 p-4" onClick={onClose}>
      <div
        className="mx-auto mt-4 flex max-h-[92vh] w-full max-w-[1000px] flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-4">
          <div className="min-w-0">
            <h3 className="text-xl font-semibold">{isResume ? "Tailored Resume" : "Cover Letter"}</h3>
            <p className="mt-0.5 text-sm text-[var(--text-secondary)]">{subtitle}</p>
            <p className="text-xs text-[var(--text-muted)]">Generated from your saved profile and this job description.</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {isResume && quality.ats_friendly && <QualityBadge label="ATS-friendly" />}
              {quality.job_tailored && <QualityBadge label="Tailored to job" />}
              {quality.unsupported_claims_removed !== undefined && <QualityBadge label="Truth-checked" />}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="inline-flex overflow-hidden rounded-md border border-line text-sm">
              <button
                type="button"
                aria-pressed={mode === "preview"}
                className={`px-3 py-1.5 ${mode === "preview" ? "bg-pine text-white" : "bg-white text-[var(--text-secondary)]"}`}
                onClick={() => setMode("preview")}
              >
                Preview
              </button>
              <button
                type="button"
                aria-pressed={mode === "edit"}
                className={`px-3 py-1.5 ${mode === "edit" ? "bg-pine text-white" : "bg-white text-[var(--text-secondary)]"}`}
                onClick={() => setMode("edit")}
              >
                Edit
              </button>
            </div>
            <button className="focus-ring rounded-md p-2" type="button" onClick={onClose} aria-label="Close document">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto bg-panel/50 p-6">
          {(warnings.length > 0 || (quality.missing_job_skills_not_claimed?.length ?? 0) > 0) && (
            <div className="mx-auto mb-4 max-w-[820px] rounded-md border border-[var(--warning-border)] bg-[var(--warning-surface)] px-3 py-2 text-xs text-[var(--warning)]">
              {warnings.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
              {(quality.missing_job_skills_not_claimed?.length ?? 0) > 0 && (
                <p>Missing skills kept out of the resume: {quality.missing_job_skills_not_claimed!.join(", ")}</p>
              )}
              {doc.unsupported_claims_removed.length > 0 && (
                <p>Removed unsupported claims: {doc.unsupported_claims_removed.join("; ")}</p>
              )}
            </div>
          )}

          {mode === "preview" ? (
            isResume ? <GeneratedResumePreview content={resume} /> : <GeneratedCoverLetterPreview content={cover} />
          ) : isResume ? (
            <ResumeEditor content={resume} onChange={setResume} />
          ) : (
            <CoverLetterEditor content={cover} onChange={setCover} />
          )}
        </div>

        <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-line bg-white px-6 py-3">
          <Button type="button" onClick={save}><Save className="h-4 w-4" /> Save document</Button>
          <Button variant="secondary" type="button" onClick={copy}>Copy text</Button>
          <Button variant="secondary" type="button" onClick={() => download("docx")}>Download DOCX</Button>
          <Button variant="secondary" type="button" onClick={() => download("pdf")}>Download PDF</Button>
          <Button variant="secondary" type="button" onClick={onClose}>Close</Button>
          {status && <span className="text-sm text-[var(--text-muted)]">{status}</span>}
        </div>
      </div>
    </div>
  );
}

function QualityBadge({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-[var(--success-border)] bg-[var(--success-surface)] px-2 py-0.5 text-[11px] font-medium text-[var(--accent)]">
      {label}
    </span>
  );
}

function ResumeEditor({ content, onChange }: { content: ResumeContent; onChange: (c: ResumeContent) => void }) {
  function set<K extends keyof ResumeContent>(key: K, value: ResumeContent[K]) {
    onChange({ ...content, [key]: value });
  }
  return (
    <div className="mx-auto grid max-w-[820px] gap-4 rounded-md bg-white p-5 text-sm shadow-sm">
      <label className="grid gap-1">
        <span className="font-medium">Professional summary</span>
        <textarea
          aria-label="Edit summary"
          className="min-h-20 rounded-md border border-line p-2"
          value={content.summary}
          onChange={(event) => set("summary", event.target.value)}
        />
      </label>
      <label className="grid gap-1">
        <span className="font-medium">Core skills (comma separated)</span>
        <input
          aria-label="Edit skills"
          className="h-10 rounded-md border border-line px-2"
          value={(content.skills?.[0]?.items ?? []).join(", ")}
          onChange={(event) =>
            set("skills", [{ category: content.skills?.[0]?.category ?? "Core Skills", items: splitCommas(event.target.value) }])
          }
        />
      </label>
      {content.experience?.map((exp, index) => (
        <label key={index} className="grid gap-1">
          <span className="font-medium">{exp.title} — {exp.company} · bullets (one per line)</span>
          <textarea
            aria-label={`Edit experience ${index + 1} bullets`}
            className="min-h-24 rounded-md border border-line p-2"
            value={exp.bullets.join("\n")}
            onChange={(event) => {
              const experience = content.experience.map((item, itemIndex) =>
                itemIndex === index ? { ...item, bullets: splitLines(event.target.value) } : item
              );
              set("experience", experience);
            }}
          />
        </label>
      ))}
      {content.projects?.map((proj, index) => (
        <label key={index} className="grid gap-1">
          <span className="font-medium">{proj.name} · bullets (one per line)</span>
          <textarea
            aria-label={`Edit project ${index + 1} bullets`}
            className="min-h-20 rounded-md border border-line p-2"
            value={proj.bullets.join("\n")}
            onChange={(event) => {
              const projects = content.projects.map((item, itemIndex) =>
                itemIndex === index ? { ...item, bullets: splitLines(event.target.value) } : item
              );
              set("projects", projects);
            }}
          />
        </label>
      ))}
    </div>
  );
}

function CoverLetterEditor({ content, onChange }: { content: CoverLetterContent; onChange: (c: CoverLetterContent) => void }) {
  return (
    <div className="mx-auto grid max-w-[820px] gap-3 rounded-md bg-white p-5 text-sm shadow-sm">
      <label className="grid gap-1">
        <span className="font-medium">Greeting</span>
        <input
          aria-label="Edit greeting"
          className="h-10 rounded-md border border-line px-2"
          value={content.greeting}
          onChange={(event) => onChange({ ...content, greeting: event.target.value })}
        />
      </label>
      {content.paragraphs.map((paragraph, index) => (
        <label key={index} className="grid gap-1">
          <span className="font-medium">Paragraph {index + 1}</span>
          <textarea
            aria-label={`Edit paragraph ${index + 1}`}
            className="min-h-24 rounded-md border border-line p-2"
            value={paragraph}
            onChange={(event) => {
              const paragraphs = content.paragraphs.map((item, itemIndex) => (itemIndex === index ? event.target.value : item));
              onChange({ ...content, paragraphs });
            }}
          />
        </label>
      ))}
      <label className="grid gap-1">
        <span className="font-medium">Signature</span>
        <input
          aria-label="Edit signature"
          className="h-10 rounded-md border border-line px-2"
          value={content.signature}
          onChange={(event) => onChange({ ...content, signature: event.target.value })}
        />
      </label>
    </div>
  );
}

function splitCommas(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function splitLines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function normalizeResume(content: Record<string, unknown>): ResumeContent {
  const c = content as Partial<ResumeContent>;
  const header = (c.header ?? {}) as Partial<ResumeContent["header"]>;
  return {
    header: {
      full_name: header.full_name ?? "",
      email: header.email ?? "",
      phone: header.phone ?? "",
      location: header.location ?? "",
      links: header.links ?? []
    },
    summary: c.summary ?? "",
    skills: c.skills ?? [],
    experience: c.experience ?? [],
    projects: c.projects ?? [],
    education: c.education ?? [],
    awards: c.awards ?? [],
    certifications: c.certifications ?? [],
    missing_skills: c.missing_skills ?? []
  };
}

function normalizeCover(content: Record<string, unknown>): CoverLetterContent {
  const c = content as Partial<CoverLetterContent>;
  return {
    date: c.date ?? "",
    recipient: c.recipient ?? "Hiring Team",
    company: c.company ?? "",
    role: c.role ?? "",
    greeting: c.greeting ?? "Dear Hiring Team,",
    paragraphs: c.paragraphs ?? [],
    closing: c.closing ?? "Best regards,",
    signature: c.signature ?? ""
  };
}

function resumeToPlainText(c: ResumeContent): string {
  const lines: string[] = [];
  if (c.header.full_name) lines.push(c.header.full_name);
  const contact = [c.header.email, c.header.phone, c.header.location, ...(c.header.links ?? [])].filter(Boolean);
  if (contact.length) lines.push(contact.join(" | "));
  const section = (title: string) => lines.push("", title.toUpperCase());
  if (c.summary) { section("Professional Summary"); lines.push(c.summary); }
  const skillsLine = (c.skills ?? []).filter((g) => g.items?.length).map((g) => (g.category ? `${g.category}: ` : "") + g.items.join(", ")).join(" | ");
  if (skillsLine) { section("Core Skills"); lines.push(skillsLine); }
  if (c.experience?.length) {
    section("Professional Experience");
    for (const exp of c.experience) {
      const meta = [exp.location, exp.dates].filter(Boolean).join(" | ");
      lines.push([exp.title, exp.company].filter(Boolean).join(" — ") + (meta ? `  (${meta})` : ""));
      for (const bullet of exp.bullets ?? []) lines.push(`  • ${bullet}`);
    }
  }
  if (c.projects?.length) {
    section("Selected Projects");
    for (const proj of c.projects) {
      const tech = (proj.technologies ?? []).join(", ");
      lines.push(proj.name + (tech ? `  (${tech})` : ""));
      for (const bullet of proj.bullets ?? []) lines.push(`  • ${bullet}`);
    }
  }
  if (c.education?.length) {
    section("Education");
    for (const edu of c.education) lines.push([edu.school, edu.degree, edu.dates, edu.details].filter(Boolean).join(", "));
  }
  const awards = [...(c.awards ?? []), ...(c.certifications ?? [])].filter((a) => a?.name);
  if (awards.length) { section("Awards & Certifications"); for (const a of awards) lines.push(`  • ${a.name}`); }
  return lines.join("\n").trim();
}

function coverToPlainText(c: CoverLetterContent): string {
  return [
    c.date,
    "",
    c.recipient,
    c.company,
    "",
    c.greeting,
    "",
    ...c.paragraphs.flatMap((p, index) => (index ? ["", p] : [p])),
    "",
    c.closing,
    c.signature
  ].filter((line) => line !== undefined).join("\n").trim();
}

function isValidApplyUrl(url: string | null | undefined): boolean {
  if (!url) {
    return false;
  }
  const low = url.toLowerCase();
  if (!low.startsWith("http://") && !low.startsWith("https://")) {
    return false;
  }
  return !["example.com", "example.org", "localhost", "127.0.0.1", "test.com", "demo.com", "placeholder"].some((host) =>
    low.includes(host)
  );
}

/**
 * Primary apply control. For a valid official URL it launches the assisted
 * application flow (prepare docs + secure session, then open the employer site);
 * otherwise it shows the same "no official link" state as before.
 */
function AssistedApplyButton({ url, onApply }: { url: string | null | undefined; onApply: () => void }) {
  if (!isValidApplyUrl(url)) {
    return <span className="inline-flex h-10 items-center rounded-md bg-panel px-4 text-sm text-[var(--text-muted)]">No official link available</span>;
  }
  return (
    <button
      type="button"
      onClick={onApply}
      className="focus-ring inline-flex h-10 items-center gap-2 rounded-md bg-pine px-4 text-sm font-medium text-white"
    >
      Apply on official site <ExternalLink className="h-4 w-4" />
    </button>
  );
}

function JobDetailsModal({
  job,
  onClose,
  onSave,
  onApply
}: {
  job: Job;
  onClose: () => void;
  onSave: () => void;
  onApply: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 p-4" onClick={onClose}>
      <div
        className="mx-auto mt-4 flex max-h-[92vh] max-w-[820px] flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line p-5">
          <div>
            <h3 className="text-xl font-semibold">{job.title}</h3>
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              {job.company}
              {job.location ? ` · ${job.location}` : ""}
            </p>
          </div>
          <button className="focus-ring rounded-md p-2" type="button" onClick={onClose} aria-label="Close details">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="overflow-auto p-5">
          <div className="flex flex-wrap items-center gap-2">
            <WorkplaceBadge type={job.workplace_type} />
            <PostedBadge postedAt={job.posted_at} />
            <SourceBadge source={job.source} />
            {job.match?.fit_label && <span className="rounded-full bg-panel px-3 py-1 text-xs font-medium text-pine">{job.match.fit_label}</span>}
          </div>

          {job.match && (
            <section className="mt-4 rounded-lg border border-line bg-panel/50 p-4">
              <h4 className="font-semibold">Why this matches</h4>
              {job.match.match_reasons.length === 0 && job.match.fit_score !== null && (
                <p className="mt-1 text-xs text-[var(--text-muted)]">Deterministic match — add more profile detail for richer reasons.</p>
              )}
              <ul className="mt-2 list-disc pl-5 text-sm leading-6">
                {job.match.match_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
              {job.match.missing_skills.length > 0 && (
                <p className="mt-2 text-sm"><span className="font-medium">Missing skills:</span> {job.match.missing_skills.join(", ")}</p>
              )}
              {job.match.risk_factors.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-sm text-[var(--danger)]">
                  {job.match.risk_factors.map((risk) => (
                    <li key={risk}>{risk}</li>
                  ))}
                </ul>
              )}
              {job.match.recommended_resume_angle && (
                <p className="mt-2 text-sm"><span className="font-medium">Resume angle:</span> {job.match.recommended_resume_angle}</p>
              )}
            </section>
          )}

          {job.responsibilities && job.responsibilities.length > 0 && (
            <section className="mt-4">
              <h4 className="font-semibold">Responsibilities</h4>
              <ul className="mt-2 list-disc pl-5 text-sm leading-6 text-[var(--text-secondary)]">
                {job.responsibilities.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}

          {job.required_skills.length > 0 && (
            <section className="mt-4">
              <h4 className="font-semibold">Requirements</h4>
              <div className="mt-2 flex flex-wrap gap-2">
                {job.required_skills.map((skill) => (
                  <span key={skill} className="rounded-full border border-line px-3 py-1 text-xs">{skill}</span>
                ))}
              </div>
            </section>
          )}

          {job.description_clean && (
            <section className="mt-4">
              <h4 className="font-semibold">Description</h4>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[var(--text-secondary)]">{job.description_clean.slice(0, 4000)}</p>
            </section>
          )}
        </div>
        <div className="flex flex-wrap gap-2 border-t border-line p-4">
          <AssistedApplyButton url={job.application_url} onApply={onApply} />
          <Button variant="secondary" type="button" onClick={onSave}><Save className="h-4 w-4" /> Save to tracker</Button>
        </div>
      </div>
    </div>
  );
}

function FitBadge({
  score,
  label,
  scoreState
}: {
  score: number | null;
  label: string | null;
  scoreState?: ScoreState | null;
}) {
  const display = getScoreDisplay(scoreState, score);

  // Non-scored lifecycle states get a neutral badge with a clear message so a
  // pending/incomplete/failed score never reads as a permanent "Not scored".
  if (display.kind !== "score") {
    return (
      <div
        data-fit-tone="none"
        data-score-state={scoreState ?? "pending"}
        className="w-24 shrink-0 rounded-xl border border-line bg-panel px-2 py-2 text-center text-[var(--text-muted)]"
        title={display.helper}
      >
        <p className="text-[10px] font-medium uppercase tracking-wide opacity-70">Fit score</p>
        {display.kind === "calculating" ? (
          <p className="flex h-8 items-center justify-center" aria-label="Calculating fit">
            <Loader2 className="h-5 w-5 animate-spin" />
          </p>
        ) : (
          <p className="text-2xl font-bold leading-tight">—</p>
        )}
        <p className="mt-0.5 text-[10px] font-semibold leading-tight">{display.label}</p>
      </div>
    );
  }

  const tone = getFitScoreTone(score);
  return (
    <div
      data-fit-tone={tone.key}
      data-score-state="scored"
      className={`w-24 shrink-0 rounded-xl border px-2 py-2 text-center ${tone.container}`}
      title={tone.description}
    >
      <p className="text-[10px] font-medium uppercase tracking-wide opacity-70">Fit score</p>
      <p className={`text-2xl font-bold leading-tight ${tone.number}`}>{score !== null ? Math.round(score) : "—"}</p>
      <p className="text-[11px] font-semibold">{label ?? tone.label}</p>
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  recruitee: "Recruitee",
  workable: "Workable",
  teamtailor: "Teamtailor",
  breezy: "Breezy",
  simplifyjobs: "SimplifyJobs"
};

function SourceBadge({ source }: { source: string | null }) {
  if (!source || source === "demo") {
    return null;
  }
  const label = SOURCE_LABELS[source.toLowerCase()] ?? source[0].toUpperCase() + source.slice(1);
  return <span className="rounded-full border border-line px-2 py-0.5 text-xs text-[var(--text-muted)]">{label}</span>;
}

function WorkplaceBadge({ type }: { type: string | null }) {
  if (!type || type === "unknown") {
    return null;
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs">
      <MapPin className="h-3 w-3" /> {type[0].toUpperCase() + type.slice(1)}
    </span>
  );
}

function PostedBadge({ postedAt }: { postedAt: string | null }) {
  const label = postedLabel(postedAt);
  if (!label) {
    return null;
  }
  return <span className="rounded-full bg-panel px-2 py-0.5 text-xs text-[var(--text-muted)]">{label}</span>;
}

function daysAgo(postedAt: string | null, now: number): number | null {
  if (!postedAt) {
    return null;
  }
  const time = new Date(postedAt).getTime();
  if (Number.isNaN(time)) {
    return null;
  }
  return Math.floor((now - time) / (1000 * 60 * 60 * 24));
}

function dateValue(postedAt: string | null): number {
  if (!postedAt) {
    return 0;
  }
  const value = new Date(postedAt).getTime();
  return Number.isNaN(value) ? 0 : value;
}

function postedLabel(postedAt: string | null): string | null {
  const days = daysAgo(postedAt, Date.now());
  if (days === null) {
    return null;
  }
  if (days <= 0) {
    return "Posted today";
  }
  if (days === 1) {
    return "Posted 1 day ago";
  }
  return `Posted ${days} days ago`;
}
