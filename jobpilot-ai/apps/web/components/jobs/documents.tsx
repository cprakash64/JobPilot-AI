"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, Save, X } from "lucide-react";
import {
  api,
  API_URL,
  type CoverLetterContent,
  type GeneratedDocument,
  type Job,
  type ResumeContent
} from "@/lib/api";
import { Button } from "@/components/Button";
import { GeneratedResumePreview } from "@/components/GeneratedResumePreview";
import { GeneratedCoverLetterPreview } from "@/components/GeneratedCoverLetterPreview";

export type DocType = "resume" | "cover_letter";

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

export function DocumentGenerationModal({ type, job }: { type: DocType; job: Job | null }) {
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

export function DocumentModal({
  doc,
  subtitle,
  onClose
}: {
  doc: GeneratedDocument;
  subtitle: string;
  onClose: () => void;
}) {
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
