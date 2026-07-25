"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  BriefcaseBusiness,
  CalendarCheck2,
  CheckCircle2,
  ChevronRight,
  CircleUserRound,
  FileText,
  Loader2,
  Sparkles,
  Target,
  Trophy
} from "lucide-react";
import { api, type Job, type JobsResponse } from "@/lib/api";
import type { ProfileWire } from "@/lib/profileForm";
import type { TrackerApplication } from "@/components/TrackerClient";

export function DashboardClient() {
  const [profile, setProfile] = useState<ProfileWire | null>(null);
  const [applications, setApplications] = useState<TrackerApplication[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      api<{ profile: ProfileWire | null }>("/profile"),
      api<{ applications: TrackerApplication[] }>("/jobs/tracker/all"),
      api<JobsResponse>("/jobs?posted_within_days=7")
    ]).then(([profileResult, trackerResult, jobsResult]) => {
      if (!active) return;
      if (profileResult.status === "fulfilled") setProfile(profileResult.value.profile);
      if (trackerResult.status === "fulfilled") setApplications(trackerResult.value.applications);
      if (jobsResult.status === "fulfilled") setJobs(jobsResult.value.jobs);
      if ([profileResult, trackerResult, jobsResult].every((result) => result.status === "rejected")) {
        setError("We couldn’t load your workspace. Refresh to try again.");
      }
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);

  const pipeline = useMemo(() => {
    const count = (statuses: string[]) => applications.filter((item) => statuses.includes(item.status)).length;
    return {
      saved: count(["saved", "ready_to_apply"]),
      active: count(["applying", "applied"]),
      interviews: count(["interview"]),
      offers: count(["offer"])
    };
  }, [applications]);

  const topMatches = useMemo(
    () =>
      [...jobs]
        .filter((job) => job.match?.fit_score != null)
        .sort((a, b) => (b.match?.fit_score ?? 0) - (a.match?.fit_score ?? 0))
        .slice(0, 3),
    [jobs]
  );

  const profileProgress = getProfileProgress(profile);
  const firstName = cleanString(profile?.preferred_first_name) || cleanString(profile?.first_name);
  const nextAction =
    profileProgress < 80
      ? {
          eyebrow: `${profileProgress}% profile complete`,
          title: "Finish your profile",
          body: "A complete profile improves matching and application accuracy.",
          href: "/profile",
          cta: "Continue profile",
          icon: CircleUserRound
        }
      : pipeline.saved > 0
        ? {
            eyebrow: `${pipeline.saved} saved`,
            title: "Move a saved role forward",
            body: "Review your strongest saved role and prepare its application.",
            href: "/tracker",
            cta: "Review saved roles",
            icon: FileText
          }
        : {
            eyebrow: `${jobs.length} fresh matches`,
            title: "Review this week’s matches",
            body: "Start with the highest-fit roles while they’re still fresh.",
            href: "/jobs",
            cta: "View matches",
            icon: Target
          };

  if (loading) {
    return (
      <div className="grid min-h-[420px] place-items-center">
        <div className="flex items-center gap-3 text-sm text-[var(--text-muted)]">
          <Loader2 className="h-5 w-5 animate-spin text-pine" />
          Loading your workspace…
        </div>
      </div>
    );
  }

  return (
    <div className="pb-10">
      <header className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-pine">Your workspace</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">
            Welcome back{firstName ? `, ${firstName}` : ""}.
          </h1>
          <p className="mt-2 text-[var(--text-muted)]">Here’s what needs your attention today.</p>
        </div>
        <Link
          href="/jobs"
          className="focus-ring inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-pine px-4 text-sm font-semibold text-white hover:bg-[var(--accent-hover)]"
        >
          <BriefcaseBusiness className="h-4 w-4" /> Find jobs
        </Link>
      </header>

      {error && (
        <p className="mt-6 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-surface)] px-4 py-3 text-sm text-[var(--danger)]">
          {error}
        </p>
      )}

      <section className="mt-8 overflow-hidden rounded-2xl border border-line bg-white">
        <div className="grid divide-y divide-line sm:grid-cols-4 sm:divide-x sm:divide-y-0">
          <Metric icon={<Sparkles />} label="Fresh matches" value={jobs.length} />
          <Metric icon={<CheckCircle2 />} label="In progress" value={pipeline.active} />
          <Metric icon={<CalendarCheck2 />} label="Interviews" value={pipeline.interviews} />
          <Metric icon={<Trophy />} label="Offers" value={pipeline.offers} />
        </div>
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.25fr_.75fr]">
        <section className="rounded-2xl border border-line bg-white p-5 sm:p-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold tracking-[-0.02em]">Application pipeline</h2>
              <p className="mt-1 text-sm text-[var(--text-muted)]">Your active search at a glance.</p>
            </div>
            <Link href="/tracker" className="focus-ring inline-flex items-center gap-1 text-sm font-semibold text-pine">
              Open tracker <ChevronRight className="h-4 w-4" />
            </Link>
          </div>

          <div className="mt-6 grid grid-cols-4 gap-2" aria-label="Application pipeline">
            <PipelineStage label="Saved" value={pipeline.saved} />
            <PipelineStage label="In progress" value={pipeline.active} />
            <PipelineStage label="Interview" value={pipeline.interviews} />
            <PipelineStage label="Offer" value={pipeline.offers} />
          </div>

          <div className="mt-7 border-t border-line pt-5">
            <h3 className="text-sm font-semibold">Recently updated</h3>
            {applications.length > 0 ? (
              <div className="mt-2 divide-y divide-line">
                {applications.slice(0, 3).map((application) => (
                  <Link
                    key={application.id}
                    href="/tracker"
                    className="focus-ring flex items-center gap-3 py-3.5"
                  >
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-panel text-pine">
                      <BriefcaseBusiness className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold">{application.job.title}</span>
                      <span className="mt-0.5 block truncate text-xs text-[var(--text-muted)]">{application.job.company}</span>
                    </span>
                    <StatusPill status={application.status} />
                  </Link>
                ))}
              </div>
            ) : (
              <EmptyLine text="Applications you save or start will appear here." href="/jobs" label="Browse jobs" />
            )}
          </div>
        </section>

        <aside className="rounded-2xl border border-[var(--success-border)] bg-[var(--success-surface)] p-6">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-white/70 text-pine">
            <nextAction.icon className="h-5 w-5" />
          </span>
          <p className="mt-8 text-xs font-semibold uppercase tracking-[0.12em] text-pine">{nextAction.eyebrow}</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.03em]">{nextAction.title}</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">{nextAction.body}</p>
          <Link
            href={nextAction.href}
            className="focus-ring mt-6 inline-flex h-11 items-center gap-2 rounded-xl bg-pine px-4 text-sm font-semibold text-white"
          >
            {nextAction.cta} <ArrowRight className="h-4 w-4" />
          </Link>
        </aside>
      </div>

      <section className="mt-6 rounded-2xl border border-line bg-white p-5 sm:p-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold tracking-[-0.02em]">Best matches this week</h2>
            <p className="mt-1 text-sm text-[var(--text-muted)]">Highest-fit roles that are still open.</p>
          </div>
          <Link href="/jobs" className="focus-ring inline-flex items-center gap-1 text-sm font-semibold text-pine">
            View all <ChevronRight className="h-4 w-4" />
          </Link>
        </div>

        {topMatches.length > 0 ? (
          <div className="mt-4 divide-y divide-line">
            {topMatches.map((job) => (
              <Link key={job.id} href="/jobs" className="focus-ring flex items-center gap-4 py-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">{job.title}</p>
                  <p className="mt-1 truncate text-sm text-[var(--text-muted)]">
                    {job.company}{job.location ? ` · ${job.location}` : ""}
                  </p>
                </div>
                <span className="rounded-xl border border-[var(--success-border)] bg-[var(--success-surface)] px-3 py-2 text-sm font-semibold text-pine">
                  {Math.round(job.match?.fit_score ?? 0)}% fit
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyLine text="Run job discovery to see your strongest matches here." href="/jobs" label="Find matches" />
        )}
      </section>
    </div>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="flex items-center gap-3 px-5 py-5">
      <span className="h-5 w-5 text-pine">{icon}</span>
      <div>
        <p className="text-2xl font-semibold leading-none">{value}</p>
        <p className="mt-1 text-xs text-[var(--text-muted)]">{label}</p>
      </div>
    </div>
  );
}

function PipelineStage({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-panel px-3 py-3 text-center">
      <p className="text-xl font-semibold">{value}</p>
      <p className="mt-1 truncate text-xs text-[var(--text-muted)]">{label}</p>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className="rounded-full border border-line px-2.5 py-1 text-xs font-medium text-[var(--text-muted)]">
      {status === "ready_to_apply" ? "Saved" : status.replaceAll("_", " ").replace(/^\w/, (letter) => letter.toUpperCase())}
    </span>
  );
}

function EmptyLine({ text, href, label }: { text: string; href: string; label: string }) {
  return (
    <div className="mt-4 flex flex-col items-start justify-between gap-3 rounded-xl bg-panel px-4 py-4 sm:flex-row sm:items-center">
      <p className="text-sm text-[var(--text-muted)]">{text}</p>
      <Link href={href} className="focus-ring shrink-0 text-sm font-semibold text-pine">{label}</Link>
    </div>
  );
}

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function getProfileProgress(profile: ProfileWire | null): number {
  if (!profile) return 0;
  const checks = [
    Boolean(cleanString(profile.first_name) || cleanString(profile.full_name)),
    Boolean(cleanString(profile.application_email)),
    Array.isArray(profile.target_roles) && profile.target_roles.length > 0,
    Array.isArray(profile.preferred_locations) && profile.preferred_locations.length > 0,
    Array.isArray(profile.skills) && profile.skills.length > 0
  ];
  return Math.round((checks.filter(Boolean).length / checks.length) * 100);
}
