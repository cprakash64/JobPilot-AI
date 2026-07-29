"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Banknote,
  Bookmark,
  BookmarkCheck,
  Briefcase,
  Building2,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Download,
  ExternalLink,
  FileText,
  Loader2,
  Mail,
  MapPin,
  Sparkles,
  X
} from "lucide-react";
import type { GeneratedDocument, Job } from "@/lib/api";
import { getScoreDisplay } from "@/lib/fitScore";
import { buildFitInsights } from "@/lib/fitInsights";
import { CompanyLogo } from "@/components/CompanyLogo";
import { PeopleWhoCanHelp } from "@/components/PeopleWhoCanHelp";
import { FitBadge, Meta, SalaryChip, SourceBadge } from "@/components/jobs/badges";
import { AssistedApplyButton } from "@/components/jobs/ApplyButton";
import {
  capitalize,
  formatEmployment,
  formatSalary,
  parseDescription,
  postedLabel,
  showsWorkplaceType,
  sourceLabel
} from "@/components/jobs/format";
import type { DocType } from "@/components/jobs/documents";
import type { TrackerStatus } from "@/components/TrackerClient";

/**
 * Four sections, not five: a tailored resume and its cover letter are one task
 * ("get my application materials ready"), and splitting them produced two thin
 * tabs that each held a single button.
 */
export const DETAIL_TABS = [
  { id: "overview", label: "Overview" },
  { id: "description", label: "Job description" },
  { id: "materials", label: "Application materials" },
  { id: "networking", label: "Networking" }
] as const;

export type DetailTab = (typeof DETAIL_TABS)[number]["id"];

const TRACKER_LABELS: Record<TrackerStatus, string> = {
  saved: "Saved",
  ready_to_apply: "Ready to apply",
  applying: "Applying",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Closed"
};

export function JobDetailPanel({
  job,
  loading,
  error,
  tab,
  onTabChange,
  trackerStatus,
  generating,
  documents,
  onClose,
  onSave,
  onApply,
  onGenerate,
  onPreviewDocument,
  onPrevious,
  onNext,
  position
}: {
  job: Job | null;
  loading: boolean;
  error: string;
  tab: DetailTab;
  onTabChange: (tab: DetailTab) => void;
  trackerStatus: TrackerStatus | null;
  generating: DocType | null;
  documents: Partial<Record<DocType, GeneratedDocument>>;
  onClose: () => void;
  onSave: () => void;
  onApply: () => void;
  onGenerate: (type: DocType) => void;
  onPreviewDocument: (doc: GeneratedDocument) => void;
  onPrevious: (() => void) | null;
  onNext: (() => void) | null;
  position: string | null;
}) {
  if (loading && !job) {
    return (
      <DetailShell onClose={onClose}>
        <p className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading this job…
        </p>
      </DetailShell>
    );
  }

  if (!job) {
    return (
      <DetailShell onClose={onClose}>
        <div className="rounded-2xl border border-line bg-white p-8">
          <h2 className="text-lg font-semibold">This job is not available</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
            {error || "It may have been withdrawn by the employer, or it is outside your current filters."}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring mt-5 inline-flex h-10 items-center gap-2 rounded-lg border border-line bg-white px-4 text-sm font-medium"
          >
            <ArrowLeft className="h-4 w-4" /> Back to jobs
          </button>
        </div>
      </DetailShell>
    );
  }

  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const posted = postedLabel(job.posted_at);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <DetailHeader
        job={job}
        salary={salary}
        posted={posted}
        trackerStatus={trackerStatus}
        onClose={onClose}
        onSave={onSave}
        onApply={onApply}
        onPrevious={onPrevious}
        onNext={onNext}
        position={position}
        tab={tab}
        onTabChange={onTabChange}
      />

      {/* The single scroll region for the detail side. Sections inside it never
        * introduce a scroller of their own, and horizontal overflow is clipped
        * so a stray wide element cannot add a second bar. */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain">
        <div className="mx-auto w-full max-w-[820px] px-6 pb-28 pt-7 sm:px-9 lg:pb-14">
          {error && (
            <p
              role="alert"
              className="mb-7 rounded-lg border border-[var(--danger-border)] bg-[var(--danger-surface)] px-3.5 py-2.5 text-sm text-[var(--danger)]"
            >
              {error}
            </p>
          )}
          <TabPanel id="overview" tab={tab}>
            <OverviewTab job={job} trackerStatus={trackerStatus} onTabChange={onTabChange} />
          </TabPanel>
          <TabPanel id="description" tab={tab}>
            <DescriptionTab job={job} posted={posted} />
          </TabPanel>
          <TabPanel id="materials" tab={tab}>
            <MaterialsTab
              job={job}
              generating={generating}
              documents={documents}
              onGenerate={onGenerate}
              onPreview={onPreviewDocument}
            />
          </TabPanel>
          <TabPanel id="networking" tab={tab}>
            {/* Mounted only while this tab is open, so opening a job never
             * reaches the people API on its own. */}
            <PeopleWhoCanHelp jobId={job.id} />
          </TabPanel>
        </div>
      </div>

      {/* Mobile keeps the primary action reachable without scrolling back up. */}
      <div className="flex shrink-0 items-center gap-2 border-t border-line bg-[var(--background)] px-4 py-3 lg:hidden">
        <AssistedApplyButton url={job.application_url} onApply={onApply} />
        <button
          type="button"
          onClick={onSave}
          className="focus-ring inline-flex h-10 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-medium"
        >
          {trackerStatus ? <BookmarkCheck className="h-4 w-4" /> : <Bookmark className="h-4 w-4" />}
          {trackerStatus ? "Saved" : "Save"}
        </button>
      </div>
    </div>
  );
}

function DetailShell({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-5 py-3">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close job details"
          className="focus-ring inline-flex h-9 items-center gap-2 rounded-lg px-2 text-sm text-[var(--text-muted)] hover:bg-panel"
        >
          <ArrowLeft className="h-4 w-4" /> Back to jobs
        </button>
      </div>
      <div className="mx-auto w-full max-w-[820px] px-6 py-10 sm:px-9">{children}</div>
    </div>
  );
}

function DetailHeader({
  job,
  salary,
  posted,
  trackerStatus,
  onClose,
  onSave,
  onApply,
  onPrevious,
  onNext,
  position,
  tab,
  onTabChange
}: {
  job: Job;
  salary: string | null;
  posted: string | null;
  trackerStatus: TrackerStatus | null;
  onClose: () => void;
  onSave: () => void;
  onApply: () => void;
  onPrevious: (() => void) | null;
  onNext: (() => void) | null;
  position: string | null;
  tab: DetailTab;
  onTabChange: (tab: DetailTab) => void;
}) {
  return (
    <header className="shrink-0 border-b border-line bg-[var(--background)]">
      <div className="mx-auto w-full max-w-[820px] px-6 pt-3 sm:px-9">
        <div className="flex h-9 items-center justify-between gap-3">
          <button
            type="button"
            onClick={onClose}
            className="focus-ring -ml-2 inline-flex h-9 items-center gap-1.5 rounded-lg px-2 text-sm text-[var(--text-muted)] hover:bg-panel lg:hidden"
          >
            <ArrowLeft className="h-4 w-4" /> Back to jobs
          </button>
          <div className="hidden items-center gap-1 lg:flex">
            {position && (
              <span className="mr-1.5 text-xs tabular-nums text-[var(--text-muted)]">{position}</span>
            )}
            <IconButton label="Previous job" onClick={onPrevious}>
              <ChevronLeft className="h-4 w-4" />
            </IconButton>
            <IconButton label="Next job" onClick={onNext}>
              <ChevronRight className="h-4 w-4" />
            </IconButton>
          </div>
          <IconButton label="Close job details" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </div>

        <div className="mt-2 flex items-start gap-4">
          <CompanyLogo
            company={job.company}
            logoUrl={job.company_logo_url}
            proxyPath={job.company_logo_proxy_path}
            companyDomain={job.company_domain}
            size={48}
          />
          <div className="min-w-0 flex-1">
            <h1 className="text-[1.6rem] font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-[1.75rem]">
              {job.title}
            </h1>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
              <span className="font-medium text-[var(--text-secondary)]">{job.company}</span>
              <SourceBadge source={job.source} />
              {trackerStatus && (
                <span className="inline-flex items-center gap-1 rounded-full bg-[var(--success-surface)] px-2 py-0.5 text-xs font-medium text-[var(--success)]">
                  <Check className="h-3 w-3" aria-hidden />
                  {TRACKER_LABELS[trackerStatus]}
                </span>
              )}
            </div>
            <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-[var(--text-muted)]">
              {job.location && <Meta icon={<MapPin className="h-3.5 w-3.5" />}>{job.location}</Meta>}
              {showsWorkplaceType(job.workplace_type, job.location) && (
                <Meta icon={<Building2 className="h-3.5 w-3.5" />}>{capitalize(job.workplace_type!)}</Meta>
              )}
              {job.employment_type && (
                <Meta icon={<Briefcase className="h-3.5 w-3.5" />}>{formatEmployment(job.employment_type)}</Meta>
              )}
              {posted && <Meta icon={<CalendarDays className="h-3.5 w-3.5" />}>{posted}</Meta>}
            </div>
          </div>
          <div className="hidden shrink-0 sm:block">
            <FitBadge
              score={job.match?.fit_score ?? null}
              label={job.match?.fit_label ?? null}
              scoreState={job.match?.score_state ?? null}
            />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2.5">
          <div className="hidden flex-wrap items-center gap-2.5 lg:flex">
            <AssistedApplyButton url={job.application_url} onApply={onApply} size="lg" />
            <button
              type="button"
              onClick={onSave}
              className="focus-ring inline-flex h-11 items-center gap-2 rounded-lg border border-line bg-white px-4 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:bg-panel"
            >
              {trackerStatus ? <BookmarkCheck className="h-4 w-4" /> : <Bookmark className="h-4 w-4" />}
              {trackerStatus ? "Saved" : "Save"}
            </button>
          </div>
          {/* Compensation is a decision factor, so it sits with the primary
            * actions — and disappears entirely when the employer published none. */}
          {salary && <SalaryChip value={salary} size="lg" />}
        </div>

        <DetailTabs tab={tab} onTabChange={onTabChange} />
      </div>
    </header>
  );
}

function IconButton({
  label,
  onClick,
  children
}: {
  label: string;
  onClick: (() => void) | null;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={!onClick}
      onClick={() => onClick?.()}
      className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-lg text-[var(--text-muted)] transition-colors hover:bg-panel hover:text-[var(--text-secondary)] disabled:pointer-events-none disabled:opacity-35"
    >
      {children}
    </button>
  );
}

function DetailTabs({ tab, onTabChange }: { tab: DetailTab; onTabChange: (tab: DetailTab) => void }) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKeyDown = (event: React.KeyboardEvent) => {
    const index = DETAIL_TABS.findIndex((item) => item.id === tab);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % DETAIL_TABS.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + DETAIL_TABS.length) % DETAIL_TABS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = DETAIL_TABS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = DETAIL_TABS[nextIndex];
    onTabChange(next.id);
    refs.current[next.id]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="Job sections"
      onKeyDown={onKeyDown}
      className="scroll-strip -mx-6 mt-5 flex gap-6 overflow-x-auto px-6 sm:-mx-9 sm:px-9"
    >
      {DETAIL_TABS.map((item) => {
        const active = item.id === tab;
        return (
          <button
            key={item.id}
            ref={(node) => {
              refs.current[item.id] = node;
            }}
            type="button"
            role="tab"
            id={`job-tab-${item.id}`}
            aria-selected={active}
            aria-controls={`job-panel-${item.id}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onTabChange(item.id)}
            className={`focus-ring -mb-px whitespace-nowrap border-b-2 pb-2.5 pt-1 text-sm font-medium transition-colors ${
              active
                ? "border-pine text-pine"
                : "border-transparent text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
            }`}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

function TabPanel({ id, tab, children }: { id: DetailTab; tab: DetailTab; children: React.ReactNode }) {
  if (id !== tab) {
    return null;
  }
  return (
    <div role="tabpanel" id={`job-panel-${id}`} aria-labelledby={`job-tab-${id}`} tabIndex={-1}>
      {children}
    </div>
  );
}

function Section({
  title,
  description,
  children
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-10 first:mt-0">
      <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">{title}</h2>
      {description && <p className="mt-1.5 text-sm leading-6 text-[var(--text-muted)]">{description}</p>}
      <div className="mt-3.5">{children}</div>
    </section>
  );
}

function OverviewTab({
  job,
  trackerStatus,
  onTabChange
}: {
  job: Job;
  trackerStatus: TrackerStatus | null;
  onTabChange: (tab: DetailTab) => void;
}) {
  const match = job.match;
  const display = getScoreDisplay(match?.score_state ?? null, match?.fit_score ?? null);
  const insights = useMemo(() => buildFitInsights(job), [job]);
  const scored = display.kind === "score" && match?.fit_score != null;

  return (
    <>
      {match?.fit_summary && (
        <p className="text-[15px] leading-7 text-[var(--text-secondary)]">{match.fit_summary}</p>
      )}

      <Section title="Your fit">
        {!scored ? (
          <p className="text-sm leading-6 text-[var(--text-muted)]">{display.helper}</p>
        ) : (
          <div className="rounded-2xl border border-line bg-white p-5">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="flex items-baseline gap-2.5">
                <span className="text-4xl font-semibold leading-none tracking-[-0.03em] text-ink">
                  {Math.round(match!.fit_score!)}
                </span>
                <span className="text-sm text-[var(--text-muted)]">/ 100</span>
                {match?.fit_label && (
                  <span className="ml-1 text-sm font-medium text-pine">{match.fit_label}</span>
                )}
              </div>
              <dl className="flex flex-wrap gap-x-7 gap-y-2">
                <ScoreFact
                  label="Skills matched"
                  value={`${insights.matchedSkills.length} of ${
                    insights.matchedSkills.length + insights.missingSkills.length || insights.matchedSkills.length
                  }`}
                />
                <ScoreFact label="Gaps" value={String(insights.missingSkills.length)} />
                <ScoreFact
                  label="Confidence"
                  value={match?.confidence != null ? `${Math.round(match.confidence * 100)}%` : "—"}
                />
              </dl>
            </div>
            {/* One overall number is what the backend produces. Presenting
              * invented per-category scores would misdescribe the assessment. */}
            <p className="mt-4 border-t border-line/70 pt-3.5 text-xs leading-5 text-[var(--text-muted)]">
              This is a single overall score from your profile and this posting
              {match?.explanation_source ? ` (${match.explanation_source} explanation)` : ""}. JobPilot does not
              break it into per-category sub-scores, so the signals below are the whole picture.
            </p>
          </div>
        )}
      </Section>

      {(insights.strengths.length > 0 || insights.matchedSkills.length > 0) && (
        <Section title="What is working for you">
          {insights.strengths.length > 0 && (
            <ul className="grid gap-2.5">
              {insights.strengths.map((reason) => (
                <li key={reason} className="flex gap-2.5 text-sm leading-6 text-[var(--text-secondary)]">
                  <Check className="mt-1 h-4 w-4 shrink-0 text-pine" aria-hidden />
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
          )}
          {insights.matchedSkills.length > 0 && (
            <div className={insights.strengths.length > 0 ? "mt-4" : ""}>
              <p className="text-xs text-[var(--text-muted)]">Required skills you already match</p>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {insights.matchedSkills.map((skill) => (
                  <li
                    key={skill}
                    className="rounded-md border border-[var(--success-border)] bg-[var(--success-surface)] px-2.5 py-1 text-xs font-medium text-[var(--success)]"
                  >
                    {skill}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Section>
      )}

      {(insights.missingSkills.length > 0 || insights.risks.length > 0) && (
        <Section
          title="What is holding it back"
          description="The specific signals this posting flagged against your profile."
        >
          {insights.missingSkills.length > 0 && (
            <div>
              <p className="text-xs text-[var(--text-muted)]">Required skills not evidenced yet</p>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {insights.missingSkills.map((skill) => (
                  <li
                    key={skill}
                    className="rounded-md border border-line bg-panel/60 px-2.5 py-1 text-xs font-medium text-[var(--text-secondary)]"
                  >
                    {skill}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {insights.risks.length > 0 && (
            <ul className={`grid gap-2.5 ${insights.missingSkills.length > 0 ? "mt-4" : ""}`}>
              {insights.risks.map((risk) => (
                <li key={risk} className="flex gap-2.5 text-sm leading-6 text-[var(--text-secondary)]">
                  <CircleAlert className="mt-1 h-4 w-4 shrink-0 text-[var(--text-muted)]" aria-hidden />
                  <span>{risk}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {insights.suggestions.length > 0 && (
        <Section
          title="How to improve your odds"
          description="Practical steps drawn from this posting and your match record. They are not predictions of a new score."
        >
          <ol className="grid gap-2.5">
            {insights.suggestions.map((suggestion) => (
              <li
                key={suggestion.id}
                className="rounded-xl border border-line bg-white p-4"
              >
                <p className="text-sm font-medium text-[var(--text-primary)]">{suggestion.title}</p>
                <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">{suggestion.body}</p>
                {suggestion.action === "materials" && (
                  <button
                    type="button"
                    onClick={() => onTabChange("materials")}
                    className="focus-ring mt-2.5 inline-flex items-center gap-1.5 rounded text-sm font-medium text-pine hover:underline"
                  >
                    Open application materials <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </button>
                )}
                {suggestion.action === "description" && (
                  <button
                    type="button"
                    onClick={() => onTabChange("description")}
                    className="focus-ring mt-2.5 inline-flex items-center gap-1.5 rounded text-sm font-medium text-pine hover:underline"
                  >
                    Read the full description <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </button>
                )}
                {suggestion.action === "profile" && (
                  <a
                    href="/profile"
                    className="focus-ring mt-2.5 inline-flex items-center gap-1.5 rounded text-sm font-medium text-pine hover:underline"
                  >
                    Update your profile <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </a>
                )}
              </li>
            ))}
          </ol>
        </Section>
      )}

      {!insights.hasSignals && !match?.fit_summary && (
        <Section title="What is working for you">
          <p className="text-sm leading-6 text-[var(--text-muted)]">
            No match detail was produced for this role yet. Refresh your matches, or add target roles and skills
            to your <a className="font-medium text-pine underline" href="/profile">profile</a> for a fuller
            explanation.
          </p>
        </Section>
      )}

      <Section title="At a glance">
        <dl className="grid gap-x-8 gap-y-3.5 sm:grid-cols-2">
          <Fact label="Seniority" value={job.seniority_level ? capitalize(job.seniority_level) : null} />
          <Fact
            label="Application status"
            value={trackerStatus ? TRACKER_LABELS[trackerStatus] : "Not saved yet"}
          />
          <Fact
            label="Listed requirements"
            value={job.required_skills.length > 0 ? `${job.required_skills.length} named skills` : null}
          />
          <Fact label="Source" value={sourceLabel(job.source)} />
        </dl>
      </Section>
    </>
  );
}

function ScoreFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium tabular-nums text-[var(--text-secondary)]">{value}</dd>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) {
    return null;
  }
  return (
    <div>
      <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
      <dd className="mt-0.5 text-sm text-[var(--text-secondary)]">{value}</dd>
    </div>
  );
}

const DESCRIPTION_PREVIEW_BLOCKS = 10;

function DescriptionTab({ job, posted }: { job: Job; posted: string | null }) {
  const [expanded, setExpanded] = useState(false);
  // The API returns sanitized plain text; this only decides layout. No HTML is
  // ever injected here.
  const blocks = useMemo(() => parseDescription(job.description_clean ?? ""), [job.description_clean]);
  const truncated = !expanded && blocks.length > DESCRIPTION_PREVIEW_BLOCKS;
  const visible = truncated ? blocks.slice(0, DESCRIPTION_PREVIEW_BLOCKS) : blocks;

  return (
    <>
      {job.required_skills.length > 0 && (
        <Section title="Requirements">
          <ul className="flex flex-wrap gap-1.5">
            {job.required_skills.map((skill) => (
              <li
                key={skill}
                className="rounded-md border border-line bg-white px-2.5 py-1 text-xs font-medium text-[var(--text-secondary)]"
              >
                {skill}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {job.preferred_skills.length > 0 && (
        <Section title="Nice to have">
          <ul className="flex flex-wrap gap-1.5">
            {job.preferred_skills.map((skill) => (
              <li key={skill} className="rounded-md border border-line px-2.5 py-1 text-xs text-[var(--text-muted)]">
                {skill}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {job.responsibilities && job.responsibilities.length > 0 && (
        <Section title="Responsibilities">
          <ul className="grid gap-2.5">
            {job.responsibilities.map((item) => (
              <li key={item} className="flex gap-2.5 text-[15px] leading-[1.65] text-[var(--text-secondary)]">
                <span aria-hidden className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-border-strong" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Full description">
        {blocks.length === 0 ? (
          <p className="text-sm leading-6 text-[var(--text-muted)]">
            The employer did not publish a description through this source. Open the official posting for the full
            text.
          </p>
        ) : (
          <div className="grid gap-4 text-[15px] leading-[1.7] text-[var(--text-secondary)]">
            {visible.map((block, index) =>
              block.kind === "paragraph" ? (
                <p key={index}>{block.text}</p>
              ) : (
                <ul key={index} className="grid gap-2.5">
                  {block.items.map((item, itemIndex) => (
                    <li key={itemIndex} className="flex gap-2.5">
                      <span aria-hidden className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-border-strong" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )
            )}
          </div>
        )}
        {blocks.length > DESCRIPTION_PREVIEW_BLOCKS && (
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            aria-expanded={expanded}
            className="focus-ring mt-5 inline-flex h-10 items-center rounded-lg border border-line bg-white px-3.5 text-sm font-medium"
          >
            {expanded ? "Show less" : "Show full description"}
          </button>
        )}
      </Section>

      <Section title="Source">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-[var(--text-muted)]">
          {sourceLabel(job.source) && <span>Listed via {sourceLabel(job.source)}</span>}
          {posted && <span>{posted}</span>}
          {job.source_url && (
            <a
              className="focus-ring inline-flex items-center gap-1.5 rounded font-medium text-pine hover:underline"
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open original posting <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </a>
          )}
        </div>
      </Section>
    </>
  );
}

const MATERIALS: { type: DocType; title: string; blurb: string; icon: typeof FileText }[] = [
  {
    type: "resume",
    title: "Tailored resume",
    blurb: "Your saved experience, re-ordered and re-worded for this posting.",
    icon: FileText
  },
  {
    type: "cover_letter",
    title: "Cover letter",
    blurb: "A focused draft grounded in your profile and this job description.",
    icon: Mail
  }
];

function MaterialsTab({
  job,
  generating,
  documents,
  onGenerate,
  onPreview
}: {
  job: Job;
  generating: DocType | null;
  documents: Partial<Record<DocType, GeneratedDocument>>;
  onGenerate: (type: DocType) => void;
  onPreview: (doc: GeneratedDocument) => void;
}) {
  return (
    <>
      <Section
        title="Application materials"
        description="Generated only from facts in your saved profile and this job description. Nothing you have not claimed is added."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          {MATERIALS.map((material) => (
            <MaterialCard
              key={material.type}
              material={material}
              document={documents[material.type]}
              busy={generating === material.type}
              // One request at a time: a second generation while one is in
              // flight would bill twice for the same document.
              disabled={generating !== null}
              onGenerate={() => onGenerate(material.type)}
              onPreview={onPreview}
            />
          ))}
        </div>
      </Section>

      {job.match?.recommended_resume_angle && (
        <Section title="Tailoring angle">
          <div className="flex gap-3 rounded-xl border border-[var(--success-border)] bg-[var(--success-surface)] p-4">
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-[var(--success)]" aria-hidden />
            <p className="text-sm leading-6 text-[var(--text-secondary)]">
              {job.match.recommended_resume_angle}
            </p>
          </div>
        </Section>
      )}

      {(job.match?.missing_skills.length ?? 0) > 0 && (
        <Section title="Kept out of your resume">
          <p className="text-sm leading-6 text-[var(--text-muted)]">
            {job.match!.missing_skills.join(", ")} — required by this job but not evidenced in your profile, so
            they are never claimed on your behalf.
          </p>
        </Section>
      )}
    </>
  );
}

function MaterialCard({
  material,
  document,
  busy,
  disabled,
  onGenerate,
  onPreview
}: {
  material: (typeof MATERIALS)[number];
  document: GeneratedDocument | undefined;
  busy: boolean;
  disabled: boolean;
  onGenerate: () => void;
  onPreview: (doc: GeneratedDocument) => void;
}) {
  const Icon = material.icon;
  const ready = Boolean(document) && !busy;
  return (
    <article className="flex flex-col rounded-2xl border border-line bg-white p-5">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${
            ready ? "bg-[var(--success-surface)] text-[var(--success)]" : "bg-panel text-[var(--text-muted)]"
          }`}
        >
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">{material.title}</h3>
          <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">{material.blurb}</p>
        </div>
      </div>

      <p aria-live="polite" className="mt-4 flex items-center gap-2 text-sm">
        {busy ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin text-pine" aria-hidden />
            <span className="text-[var(--text-secondary)]">Generating…</span>
          </>
        ) : ready ? (
          <>
            <Check className="h-3.5 w-3.5 text-[var(--success)]" aria-hidden />
            <span className="truncate text-[var(--text-secondary)]">Ready · {document!.title}</span>
          </>
        ) : (
          <span className="text-[var(--text-muted)]">Not generated yet</span>
        )}
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        {ready ? (
          <>
            <button
              type="button"
              onClick={() => onPreview(document!)}
              className="focus-ring inline-flex h-10 items-center gap-2 rounded-lg bg-pine px-3.5 text-sm font-medium text-white transition-colors hover:bg-[var(--accent-hover)]"
            >
              <Download className="h-4 w-4" aria-hidden /> Preview and download
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={onGenerate}
              className="focus-ring inline-flex h-10 items-center rounded-lg border border-line bg-white px-3.5 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
            >
              Regenerate
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={disabled}
            onClick={onGenerate}
            className="focus-ring inline-flex h-10 items-center gap-2 rounded-lg bg-pine px-3.5 text-sm font-medium text-white transition-colors hover:bg-[var(--accent-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
            {material.type === "resume" ? "Generate tailored resume" : "Generate cover letter"}
          </button>
        )}
      </div>
    </article>
  );
}

/** Escape closes the workspace detail view when nothing else owns the key. */
export function useEscapeToClose(active: boolean, onClose: () => void) {
  const handler = useCallback(
    (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (!active) return;
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [active, handler]);
}
