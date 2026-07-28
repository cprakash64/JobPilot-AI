"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BriefcaseBusiness,
  CalendarDays,
  CheckCircle2,
  ExternalLink,
  Loader2,
  MapPin,
  Search,
  Trophy
} from "lucide-react";
import Link from "next/link";
import { CompanyLogo } from "@/components/CompanyLogo";
import { api, type Job } from "@/lib/api";

export type TrackerStatus =
  | "saved"
  | "ready_to_apply"
  | "applying"
  | "applied"
  | "interview"
  | "offer"
  | "rejected";

export type TrackerApplication = {
  id: number;
  job_id: number;
  status: TrackerStatus;
  notes?: string | null;
  applied_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  job: Pick<
    Job,
    | "id"
    | "title"
    | "company"
    | "company_domain"
    | "company_logo_url"
    | "company_logo_proxy_path"
    | "location"
    | "workplace_type"
    | "employment_type"
    | "posted_at"
    | "application_url"
    | "match"
  >;
};

type TrackerFilter = "all" | "applied" | "interview" | "offer" | "rejected";

const STATUS_OPTIONS: { value: TrackerStatus; label: string }[] = [
  { value: "applied", label: "Applied" },
  { value: "interview", label: "Interview" },
  { value: "offer", label: "Offer / selected" },
  { value: "rejected", label: "Rejected" }
];

const FILTERS: { value: TrackerFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "applied", label: "Applied" },
  { value: "interview", label: "Interviews" },
  { value: "offer", label: "Offers" },
  { value: "rejected", label: "Rejected" }
];

export function TrackerClient() {
  const [applications, setApplications] = useState<TrackerApplication[]>([]);
  const [filter, setFilter] = useState<TrackerFilter>("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api<{ applications: TrackerApplication[] }>("/jobs/tracker/submitted")
      .then((result) => {
        if (!active) return;
        setError("");
        setApplications(result.applications);
      })
      .catch((loadError: unknown) => {
        if (!active) return;
        setError(loadError instanceof Error ? loadError.message : "Could not load applications.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function update(application: TrackerApplication, status: TrackerStatus) {
    setUpdatingId(application.id);
    setError("");
    try {
      await api(`/jobs/${application.job_id}/tracker`, {
        method: "PUT",
        body: JSON.stringify({ status })
      });
      setApplications((current) =>
        current.map((row) =>
          row.id === application.id
            ? {
                ...row,
                status,
                applied_at:
                  row.applied_at ??
                  (["applied", "interview", "offer", "rejected"].includes(status)
                    ? new Date().toISOString()
                    : null)
              }
            : row
        )
      );
      setMessage(`Moved to ${statusLabel(status)}.`);
      window.setTimeout(() => setMessage(""), 2500);
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Could not update status.");
    } finally {
      setUpdatingId(null);
    }
  }

  const counts = useMemo(
    () => ({
      all: applications.length,
      applied: applications.filter((row) => row.status === "applied").length,
      interview: applications.filter((row) => row.status === "interview").length,
      offer: applications.filter((row) => row.status === "offer").length
    }),
    [applications]
  );

  const visible = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return applications.filter((application) => {
      const matchesFilter =
        filter === "all"
          ? true
          : application.status === filter;
      const matchesQuery =
        !normalizedQuery ||
        `${application.job.title} ${application.job.company}`
          .toLowerCase()
          .includes(normalizedQuery);
      return matchesFilter && matchesQuery;
    });
  }, [applications, filter, query]);

  if (loading) {
    return (
      <div className="flex min-h-56 items-center justify-center rounded-2xl border border-line bg-white">
        <Loader2 className="h-6 w-6 animate-spin text-pine" />
        <span className="ml-2 text-sm text-[var(--text-muted)]">Loading your applications…</span>
      </div>
    );
  }

  return (
    <section>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          icon={<BriefcaseBusiness className="h-5 w-5" />}
          label="Total tracked"
          value={counts.all}
        />
        <SummaryCard
          icon={<CheckCircle2 className="h-5 w-5" />}
          label="Applied"
          value={counts.applied}
        />
        <SummaryCard
          icon={<CalendarDays className="h-5 w-5" />}
          label="Interviews"
          value={counts.interview}
        />
        <SummaryCard icon={<Trophy className="h-5 w-5" />} label="Offers" value={counts.offer} />
      </div>

      <div className="mt-5 flex flex-col gap-3 rounded-2xl border border-line bg-white p-3 shadow-sm lg:flex-row lg:items-center lg:justify-between">
        <div className="flex gap-1 overflow-x-auto" aria-label="Application status filters">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => setFilter(item.value)}
              className={`focus-ring whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition ${
                filter === item.value
                  ? "bg-pine text-white"
                  : "text-[var(--text-secondary)] hover:bg-panel"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <label className="relative block w-full lg:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[var(--text-muted)]" />
          <span className="sr-only">Search applications</span>
          <input
            aria-label="Search applications"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search company or role"
            className="h-10 w-full rounded-lg border border-line bg-panel/30 pl-9 pr-3 text-sm"
          />
        </label>
      </div>

      {(message || error) && (
        <p
          role="status"
          className={`mt-3 text-sm ${error ? "text-[var(--danger)]" : "text-pine"}`}
        >
          {error || message}
        </p>
      )}

      <div className="mt-4 grid gap-3">
        {visible.map((application) => (
          <ApplicationCard
            key={application.id}
            application={application}
            updating={updatingId === application.id}
            onUpdate={(status) => update(application, status)}
          />
        ))}
      </div>

      {visible.length === 0 && (
        <div className="mt-4 rounded-2xl border border-dashed border-line bg-white px-6 py-14 text-center">
          <BriefcaseBusiness className="mx-auto h-9 w-9 text-[var(--text-muted)]" />
          <h2 className="mt-3 font-semibold text-ink">
            {applications.length === 0 ? "No applications yet" : "No applications found"}
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-[var(--text-muted)]">
            {applications.length === 0
              ? "Applications appear here after you confirm that you submitted them."
              : "Try another status or search term."}
          </p>
          {applications.length === 0 && (
            <Link className="mt-4 inline-flex rounded-lg bg-pine px-4 py-2 text-sm font-medium text-white" href="/jobs">
              Find jobs
            </Link>
          )}
        </div>
      )}
    </section>
  );
}

function SummaryCard({
  icon,
  label,
  value
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-2xl border border-line bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <span className="rounded-xl bg-[var(--success-surface)] p-2 text-pine">{icon}</span>
        <span className="text-2xl font-semibold text-ink">{value}</span>
      </div>
      <p className="mt-3 text-sm font-medium text-[var(--text-secondary)]">{label}</p>
    </div>
  );
}

function ApplicationCard({
  application,
  updating,
  onUpdate
}: {
  application: TrackerApplication;
  updating: boolean;
  onUpdate: (status: TrackerStatus) => void;
}) {
  const { job } = application;
  const fitScore = job.match?.fit_score;
  return (
    <article className="rounded-2xl border border-line bg-white p-4 shadow-sm transition-shadow hover:shadow-md sm:p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <CompanyLogo
            company={job.company}
            logoUrl={job.company_logo_url}
            proxyPath={job.company_logo_proxy_path}
            companyDomain={job.company_domain}
          />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-semibold text-[var(--text-secondary)]">{job.company}</p>
              <StatusBadge status={application.status} />
            </div>
            <h2 className="mt-1 text-lg font-semibold leading-snug text-ink">{job.title}</h2>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-[var(--text-muted)]">
              {job.location && (
                <span className="inline-flex items-center gap-1">
                  <MapPin className="h-3.5 w-3.5" />
                  {job.location}
                </span>
              )}
              {application.applied_at && (
                <span className="inline-flex items-center gap-1">
                  <CalendarDays className="h-3.5 w-3.5" />
                  Applied {formatDate(application.applied_at)}
                </span>
              )}
              {fitScore != null && <span>{Math.round(fitScore)}% fit</span>}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 lg:justify-end">
          <label className="relative">
            <span className="sr-only">Update status for {job.title}</span>
            <select
              aria-label={`Update status for ${job.title}`}
              value={application.status === "ready_to_apply" ? "saved" : application.status}
              disabled={updating}
              onChange={(event) => onUpdate(event.target.value as TrackerStatus)}
              className="h-10 rounded-lg border border-line bg-white px-3 pr-8 text-sm font-medium text-[var(--text-secondary)]"
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <a
            href={job.application_url}
            target="_blank"
            rel="noopener noreferrer"
            className="focus-ring inline-flex h-10 items-center gap-1.5 rounded-lg border border-line px-3 text-sm font-medium text-[var(--text-secondary)] hover:bg-panel"
          >
            View job <ExternalLink className="h-3.5 w-3.5" />
          </a>
          {updating && <Loader2 className="h-4 w-4 animate-spin text-pine" />}
        </div>
      </div>
    </article>
  );
}

function StatusBadge({ status }: { status: TrackerStatus }) {
  const tone =
    status === "offer"
      ? "border-[var(--success-border)] bg-[var(--success-surface)] text-[var(--success)]"
      : status === "interview"
        ? "border-sky-300/40 bg-sky-500/10 text-sky-500"
        : status === "rejected"
          ? "border-[var(--danger-border)] bg-[var(--danger-surface)] text-[var(--danger)]"
          : status === "applied" || status === "applying"
            ? "border-[var(--warning-border)] bg-[var(--warning-surface)] text-[var(--warning)]"
            : "border-line bg-panel text-[var(--text-muted)]";
  return (
    <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`}>
      {statusLabel(status)}
    </span>
  );
}

function statusLabel(status: TrackerStatus): string {
  if (status === "offer") return "Offer / selected";
  if (status === "ready_to_apply") return "Saved";
  return status[0].toUpperCase() + status.slice(1).replaceAll("_", " ");
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recently";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}
