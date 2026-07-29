"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Banknote,
  Bookmark,
  BookmarkCheck,
  Briefcase,
  Building2,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  Loader2,
  Mail,
  MapPin,
  X
} from "lucide-react";
import type { GeneratedDocument, Job } from "@/lib/api";
import { getScoreDisplay } from "@/lib/fitScore";
import { CompanyLogo } from "@/components/CompanyLogo";
import { PeopleWhoCanHelp } from "@/components/PeopleWhoCanHelp";
import { FitBadge, Meta, SourceBadge } from "@/components/jobs/badges";
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

export const DETAIL_TABS = [
  { id: "overview", label: "Overview" },
  { id: "description", label: "Job description" },
  { id: "resume", label: "Resume" },
  { id: "cover_letter", label: "Cover letter" },
  { id: "networking", label: "Networking" }
] as const;

export type DetailTab = (typeof DETAIL_TABS)[number]["id"];

export function isDetailTab(value: string | null | undefined): value is DetailTab {
  return DETAIL_TABS.some((tab) => tab.id === value);
}

const TRACKER_LABELS: Record<TrackerStatus, string> = {
  saved: "Saved to tracker",
  ready_to_apply: "Ready to apply",
  applying: "Application in progress",
  applied: "Applied",
  interview: "Interview stage",
  offer: "Offer",
  rejected: "Closed — rejected"
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
        <div className="rounded-xl border border-line bg-white p-6">
          <h2 className="text-lg font-semibold">This job is not available</h2>
          <p className="mt-2 text-sm text-[var(--text-muted)]">
            {error || "It may have been withdrawn by the employer, or it is outside your current filters."}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring mt-4 inline-flex h-10 items-center gap-2 rounded-md border border-line bg-white px-4 text-sm font-medium"
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

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="mx-auto w-full max-w-[860px] px-5 pb-24 pt-6 sm:px-8 lg:pb-10">
          {error && (
            <p role="alert" className="mb-6 rounded-lg border border-[var(--danger-border)] bg-[var(--danger-surface)] px-3 py-2 text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
          <TabPanel id="overview" tab={tab}>
            <OverviewTab job={job} salary={salary} posted={posted} trackerStatus={trackerStatus} />
          </TabPanel>
          <TabPanel id="description" tab={tab}>
            <DescriptionTab job={job} posted={posted} />
          </TabPanel>
          <TabPanel id="resume" tab={tab}>
            <DocumentTab
              type="resume"
              job={job}
              generating={generating}
              document={documents.resume}
              onGenerate={onGenerate}
              onPreview={onPreviewDocument}
            />
          </TabPanel>
          <TabPanel id="cover_letter" tab={tab}>
            <DocumentTab
              type="cover_letter"
              job={job}
              generating={generating}
              document={documents.cover_letter}
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
      <div className="sticky bottom-0 z-10 flex items-center gap-2 border-t border-line bg-white/95 px-4 py-3 backdrop-blur lg:hidden">
        <AssistedApplyButton url={job.application_url} onApply={onApply} />
        <button
          type="button"
          onClick={onSave}
          className="focus-ring inline-flex h-10 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-medium"
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
      <div className="flex items-center gap-2 border-b border-line px-5 py-3">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close job details"
          className="focus-ring inline-flex h-9 items-center gap-2 rounded-md px-2 text-sm text-[var(--text-muted)] hover:bg-panel"
        >
          <ArrowLeft className="h-4 w-4" /> Back to jobs
        </button>
      </div>
      <div className="mx-auto w-full max-w-[860px] px-5 py-8 sm:px-8">{children}</div>
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
      <div className="mx-auto w-full max-w-[860px] px-5 pt-4 sm:px-8">
        <div className="flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={onClose}
            className="focus-ring inline-flex h-9 items-center gap-2 rounded-md px-2 text-sm text-[var(--text-muted)] hover:bg-panel lg:hidden"
          >
            <ArrowLeft className="h-4 w-4" /> Back to jobs
          </button>
          <div className="hidden items-center gap-1 lg:flex">
            {position && <span className="mr-2 text-xs text-[var(--text-muted)]">{position}</span>}
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

        <div className="mt-3 flex items-start gap-4">
          <CompanyLogo
            company={job.company}
            logoUrl={job.company_logo_url}
            proxyPath={job.company_logo_proxy_path}
            companyDomain={job.company_domain}
            size={52}
          />
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold leading-tight tracking-tight text-ink sm:text-[1.75rem]">
              {job.title}
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-[var(--text-secondary)]">
              <span className="font-medium">{job.company}</span>
              <SourceBadge source={job.source} />
              {trackerStatus && (
                <span className="rounded-full bg-[var(--success-surface)] px-2 py-0.5 text-xs font-medium text-[var(--success)]">
                  {TRACKER_LABELS[trackerStatus]}
                </span>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-[var(--text-muted)]">
              {job.location && <Meta icon={<MapPin className="h-3.5 w-3.5" />}>{job.location}</Meta>}
              {showsWorkplaceType(job.workplace_type, job.location) && (
                <Meta icon={<Building2 className="h-3.5 w-3.5" />}>{capitalize(job.workplace_type!)}</Meta>
              )}
              {job.employment_type && (
                <Meta icon={<Briefcase className="h-3.5 w-3.5" />}>{formatEmployment(job.employment_type)}</Meta>
              )}
              {posted && <Meta icon={<CalendarDays className="h-3.5 w-3.5" />}>{posted}</Meta>}
              {salary && <Meta icon={<Banknote className="h-3.5 w-3.5" />}>{salary}</Meta>}
            </div>
          </div>
          <div className="hidden sm:block">
            <FitBadge
              score={job.match?.fit_score ?? null}
              label={job.match?.fit_label ?? null}
              scoreState={job.match?.score_state ?? null}
            />
          </div>
        </div>

        <div className="mt-4 hidden flex-wrap items-center gap-2 lg:flex">
          <AssistedApplyButton url={job.application_url} onApply={onApply} size="lg" />
          <button
            type="button"
            onClick={onSave}
            className="focus-ring inline-flex h-11 items-center gap-2 rounded-md border border-line bg-white px-4 text-sm font-medium text-[var(--text-secondary)] hover:bg-panel"
          >
            {trackerStatus ? <BookmarkCheck className="h-4 w-4" /> : <Bookmark className="h-4 w-4" />}
            {trackerStatus ? "Saved" : "Save"}
          </button>
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
      className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-md text-[var(--text-muted)] hover:bg-panel disabled:opacity-40"
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
      className="-mx-5 mt-4 flex gap-1 overflow-x-auto px-5 sm:-mx-8 sm:px-8"
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
            className={`focus-ring whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors ${
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
    <section className="mt-8 first:mt-0">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">{title}</h2>
      {description && <p className="mt-1 text-sm text-[var(--text-muted)]">{description}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function OverviewTab({
  job,
  salary,
  posted,
  trackerStatus
}: {
  job: Job;
  salary: string | null;
  posted: string | null;
  trackerStatus: TrackerStatus | null;
}) {
  const match = job.match;
  const display = getScoreDisplay(match?.score_state ?? null, match?.fit_score ?? null);
  const missing = match?.missing_skills ?? [];
  const missingLower = new Set(missing.map((skill) => skill.toLowerCase()));
  const matched = (job.required_skills ?? []).filter((skill) => !missingLower.has(skill.toLowerCase()));

  return (
    <>
      {match?.fit_summary && (
        <p className="text-base leading-7 text-[var(--text-secondary)]">{match.fit_summary}</p>
      )}

      <Section title="Fit score">
        {display.kind !== "score" ? (
          <p className="text-sm text-[var(--text-muted)]">{display.helper}</p>
        ) : (
          <div className="rounded-xl border border-line bg-white p-4">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-3xl font-semibold text-ink">{Math.round(match!.fit_score!)}</span>
              <span className="text-sm text-[var(--text-muted)]">out of 100</span>
              {match?.fit_label && <span className="text-sm font-medium text-pine">{match.fit_label}</span>}
            </div>
            <dl className="mt-4 grid gap-3 sm:grid-cols-3">
              <ScoreFact
                label="Required skills matched"
                value={`${matched.length} of ${(job.required_skills ?? []).length || matched.length}`}
              />
              <ScoreFact label="Gaps found" value={String(missing.length)} />
              <ScoreFact
                label="Confidence"
                value={match?.confidence != null ? `${Math.round(match.confidence * 100)}%` : "Not reported"}
              />
            </dl>
            {/* The backend produces one overall score. Inventing per-category
             * numbers would misrepresent how this role was assessed. */}
            <p className="mt-4 text-xs leading-5 text-[var(--text-muted)]">
              JobPilot produces a single overall score from your profile and this job’s requirements
              {match?.explanation_source ? ` (${match.explanation_source} explanation)` : ""}. Category-level
              sub-scores are not calculated, so only the signals above are shown.
            </p>
          </div>
        )}
      </Section>

      <Section title="Why this matches">
        {match && match.match_reasons.length > 0 ? (
          <ul className="grid gap-2 text-sm leading-6 text-[var(--text-secondary)]">
            {match.match_reasons.map((reason) => (
              <li key={reason} className="flex gap-2">
                <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-pine/70" />
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">
            No written explanation was produced for this role. Add more profile detail for richer reasons.
          </p>
        )}
      </Section>

      {(matched.length > 0 || missing.length > 0) && (
        <Section
          title="Qualifications"
          description="Derived from this job’s required skills and the gaps found against your profile."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="text-sm font-medium text-[var(--text-secondary)]">Matched</p>
              {matched.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {matched.map((skill) => (
                    <li
                      key={skill}
                      className="rounded-full border border-[var(--success-border)] bg-[var(--success-surface)] px-2.5 py-1 text-xs text-[var(--success)]"
                    >
                      {skill}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-[var(--text-muted)]">None identified.</p>
              )}
            </div>
            <div>
              <p className="text-sm font-medium text-[var(--text-secondary)]">Missing</p>
              {missing.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {missing.map((skill) => (
                    <li key={skill} className="rounded-full border border-line px-2.5 py-1 text-xs text-[var(--text-muted)]">
                      {skill}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-[var(--text-muted)]">No gaps were flagged.</p>
              )}
            </div>
          </div>
        </Section>
      )}

      {match && match.risk_factors.length > 0 && (
        <Section title="Things to check">
          <ul className="grid gap-2 text-sm leading-6 text-[var(--danger)]">
            {match.risk_factors.map((risk) => (
              <li key={risk} className="flex gap-2">
                <AlertTriangle className="mt-1 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>{risk}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {job.responsibilities && job.responsibilities.length > 0 && (
        <Section title="Key responsibilities">
          <ul className="grid gap-2 text-sm leading-6 text-[var(--text-secondary)]">
            {job.responsibilities.slice(0, 8).map((item) => (
              <li key={item} className="flex gap-2">
                <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-border-strong" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Role details">
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          <Fact label="Company" value={job.company} />
          <Fact label="Location" value={job.location} />
          <Fact
            label="Workplace"
            value={job.workplace_type && job.workplace_type !== "unknown" ? capitalize(job.workplace_type) : null}
          />
          <Fact label="Employment" value={job.employment_type ? formatEmployment(job.employment_type) : null} />
          <Fact label="Seniority" value={job.seniority_level ? capitalize(job.seniority_level) : null} />
          <Fact label="Posted" value={posted} />
          {/* Only stated when the employer published a range. */}
          {salary && <Fact label="Salary" value={salary} />}
          <Fact label="Source" value={sourceLabel(job.source)} />
          <Fact
            label="Application status"
            value={trackerStatus ? TRACKER_LABELS[trackerStatus] : "Not saved yet"}
          />
        </dl>
      </Section>
    </>
  );
}

function ScoreFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-[var(--text-secondary)]">{value}</dd>
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

const DESCRIPTION_PREVIEW_BLOCKS = 8;

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
              <li key={skill} className="rounded-full border border-line px-2.5 py-1 text-xs text-[var(--text-secondary)]">
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
              <li key={skill} className="rounded-full border border-line px-2.5 py-1 text-xs text-[var(--text-muted)]">
                {skill}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Full description">
        {blocks.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">
            The employer did not publish a description through this source. Open the official posting for the full text.
          </p>
        ) : (
          <div className="grid gap-4 text-[15px] leading-[1.65] text-[var(--text-secondary)]">
            {visible.map((block, index) =>
              block.kind === "paragraph" ? (
                <p key={index}>{block.text}</p>
              ) : (
                <ul key={index} className="grid gap-2 pl-1">
                  {block.items.map((item, itemIndex) => (
                    <li key={itemIndex} className="flex gap-2">
                      <span aria-hidden className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-border-strong" />
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
            className="focus-ring mt-4 inline-flex h-10 items-center rounded-md border border-line bg-white px-3 text-sm font-medium"
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
              className="focus-ring inline-flex items-center gap-1 rounded font-medium text-pine underline"
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open original posting <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
        </div>
      </Section>
    </>
  );
}

const DOC_COPY: Record<DocType, { title: string; blurb: string; cta: string; icon: React.ReactNode }> = {
  resume: {
    title: "Tailored resume",
    blurb:
      "Built only from facts in your saved profile, re-ordered and re-worded for this role. Nothing you have not claimed is added.",
    cta: "Generate tailored resume",
    icon: <FileText className="h-4 w-4" />
  },
  cover_letter: {
    title: "Cover letter",
    blurb:
      "A focused first draft grounded in your profile and this job description. Review and edit before sending.",
    cta: "Generate cover letter",
    icon: <Mail className="h-4 w-4" />
  }
};

function DocumentTab({
  type,
  job,
  generating,
  document,
  onGenerate,
  onPreview
}: {
  type: DocType;
  job: Job;
  generating: DocType | null;
  document: GeneratedDocument | undefined;
  onGenerate: (type: DocType) => void;
  onPreview: (doc: GeneratedDocument) => void;
}) {
  const copy = DOC_COPY[type];
  const busy = generating === type;
  return (
    <>
      <Section title={copy.title}>
        <p className="text-sm leading-6 text-[var(--text-secondary)]">{copy.blurb}</p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            // A second request while one is in flight would bill twice for the
            // same document.
            disabled={generating !== null}
            onClick={() => onGenerate(type)}
            className="focus-ring inline-flex h-11 items-center gap-2 rounded-md bg-pine px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : copy.icon}
            {busy ? "Generating…" : document ? `Regenerate ${type === "resume" ? "resume" : "cover letter"}` : copy.cta}
          </button>
          {document && (
            <button
              type="button"
              onClick={() => onPreview(document)}
              className="focus-ring inline-flex h-11 items-center gap-2 rounded-md border border-line bg-white px-4 text-sm font-medium"
            >
              Preview and download
            </button>
          )}
        </div>
        <p aria-live="polite" className="mt-3 text-sm text-[var(--text-muted)]">
          {busy
            ? "Generating from your profile and this job description…"
            : document
              ? `Ready: ${document.title}`
              : "Not generated for this job yet."}
        </p>
      </Section>

      {type === "resume" && job.match?.recommended_resume_angle && (
        <Section title="Tailoring angle">
          <p className="text-sm leading-6 text-[var(--text-secondary)]">{job.match.recommended_resume_angle}</p>
        </Section>
      )}

      {type === "resume" && (job.match?.missing_skills.length ?? 0) > 0 && (
        <Section title="Kept out of the resume">
          <p className="text-sm leading-6 text-[var(--text-muted)]">
            {job.match!.missing_skills.join(", ")} — these are required by the job but not evidenced in your profile, so
            they are never claimed on your behalf.
          </p>
        </Section>
      )}
    </>
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
