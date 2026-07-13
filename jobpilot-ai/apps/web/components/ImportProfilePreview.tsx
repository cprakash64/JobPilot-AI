"use client";

import { useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { AlertTriangle, Check, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/Button";

export type ImportSection =
  | "basic_info"
  | "job_targets"
  | "education"
  | "experience"
  | "projects"
  | "skills"
  | "certifications"
  | "awards"
  | "links";

export type ImportApplyMode = "all" | "selected";

type BasicInfoDraft = {
  full_name: string;
  headline: string;
  email: string;
  phone: string;
  location_city: string;
  location_state: string;
  location_country: string;
  linkedin_url: string;
  github_url: string;
  portfolio_url: string;
  work_authorization_status: string;
  requires_sponsorship: boolean | null;
};

type JobTargetsDraft = {
  target_roles: string[];
  target_levels: string[];
  preferred_locations: string[];
  work_preference: string;
};

type EducationDraft = {
  school: string;
  degree: string;
  major: string;
  minor: string;
  start_date: string;
  end_date: string;
  gpa: string;
  honors: string[];
  coursework: string[];
};

type ExperienceDraft = {
  company: string;
  title: string;
  location: string;
  start_date: string;
  end_date: string;
  currently_working: boolean;
  bullets: string[];
  technologies: string[];
  measurable_impact: string[];
};

type ProjectDraft = {
  name: string;
  subtitle: string;
  description: string;
  bullets: string[];
  technologies: string[];
  links: string[];
  start_date: string;
  end_date: string;
};

type CertificationDraft = {
  name: string;
  issuer: string;
  issue_date: string;
  expiration_date: string;
  credential_url: string;
};

type AwardDraft = {
  name: string;
  issuer: string;
  date: string;
  description: string;
};

type SkillGroupDraft = {
  category: string;
  items: string[];
};

export type EditableImportDraft = {
  basic_info: BasicInfoDraft;
  summary: string;
  job_targets: JobTargetsDraft;
  education: EducationDraft[];
  experience: ExperienceDraft[];
  projects: ProjectDraft[];
  skills: string[];
  skill_groups: SkillGroupDraft[];
  certifications: CertificationDraft[];
  awards: AwardDraft[];
  links: {
    linkedin_url: string;
    github_url: string;
    portfolio_url: string;
    other_links: string[];
  };
  raw_text_preview: string;
  confidence_warnings: string[];
  missing_fields: string[];
  source_type: string;
  low_confidence_fields: string[];
};

type ImportProfilePreviewProps = {
  draft: Record<string, unknown>;
  currentProfile: object;
  currentCareer: object;
  saving?: boolean;
  onApply: (
    draft: EditableImportDraft,
    sections: ImportSection[],
    overwriteConflicts: boolean,
    mode: ImportApplyMode
  ) => void;
  onCancel: () => void;
};

const sectionLabels: { key: ImportSection; label: string }[] = [
  { key: "basic_info", label: "Header" },
  { key: "skills", label: "Skills" },
  { key: "experience", label: "Experience" },
  { key: "projects", label: "Projects" },
  { key: "education", label: "Education" },
  { key: "awards", label: "Awards" },
  { key: "certifications", label: "Certifications" },
  { key: "job_targets", label: "Targets" },
  { key: "links", label: "Links" }
];

const allSections = sectionLabels.map((section) => section.key);

export function ImportProfilePreview({
  draft,
  currentProfile,
  currentCareer,
  saving = false,
  onApply,
  onCancel
}: ImportProfilePreviewProps) {
  const [editableDraft, setEditableDraft] = useState<EditableImportDraft>(() => normalizeImportDraft(draft));
  const [selected, setSelected] = useState<ImportSection[]>(allSections);
  const [editing, setEditing] = useState<ImportSection[]>([]);
  const [overwriteConflicts, setOverwriteConflicts] = useState(false);
  const [showRawText, setShowRawText] = useState(false);

  const warnings = useMemo(
    () => uniqueStrings([...editableDraft.confidence_warnings]),
    [editableDraft.confidence_warnings]
  );
  const conflicts = useMemo(
    () => findConflicts(editableDraft, currentProfile, currentCareer),
    [editableDraft, currentProfile, currentCareer]
  );

  function toggle(section: ImportSection) {
    setSelected((current) =>
      current.includes(section) ? current.filter((item) => item !== section) : [...current, section]
    );
  }

  function toggleEdit(section: ImportSection) {
    setEditing((current) =>
      current.includes(section) ? current.filter((item) => item !== section) : [...current, section]
    );
  }

  function apply(sections: ImportSection[], mode: ImportApplyMode) {
    if (
      overwriteConflicts &&
      conflicts.length > 0 &&
      typeof window !== "undefined" &&
      !window.confirm("Imported values will replace conflicting saved data. Continue?")
    ) {
      return;
    }
    onApply(editableDraft, sections, overwriteConflicts, mode);
  }

  const shared = { draft: editableDraft, setDraft: setEditableDraft, selected, editing, toggle, toggleEdit };

  return (
    <div className="flex max-h-[80vh] flex-col">
      <div className="sticky top-0 z-10 border-b border-line bg-white/95 px-1 py-4 backdrop-blur">
        <div className="mx-auto max-w-[880px]">
          <h3 className="text-xl font-semibold">Review imported profile</h3>
          <p className="mt-1 text-sm text-[#5d675f]">
            Review and edit the imported resume before saving it to your profile.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {sectionLabels.map((section) => (
              <label
                key={section.key}
                className="inline-flex h-8 items-center gap-2 rounded-full border border-line px-3 text-xs font-medium"
              >
                <input
                  type="checkbox"
                  aria-label={section.label}
                  checked={selected.includes(section.key)}
                  onChange={() => toggle(section.key)}
                />
                {section.label}
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-panel/60 px-1 py-5">
        <div className="mx-auto grid w-full max-w-[880px] gap-4">
          {warnings.length > 0 && (
            <section className="rounded-lg border border-[#ead191] bg-[#fff9e8] p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[#9a6a00]" />
                <div>
                  <h4 className="font-semibold">Warnings</h4>
                  <ul className="mt-2 list-disc pl-5 text-sm leading-6 text-[#5b4a17]">
                    {warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </section>
          )}

          {conflicts.length > 0 && (
            <section className="rounded-lg border border-line bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h4 className="font-semibold">Existing data conflicts</h4>
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={overwriteConflicts}
                    onChange={(event) => setOverwriteConflicts(event.target.checked)}
                  />
                  Replace conflicting saved data
                </label>
              </div>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {conflicts.map((conflict) => (
                  <div key={conflict.field} className="rounded-md border border-line bg-panel p-3 text-sm">
                    <p className="font-medium">{conflict.field}</p>
                    <p className="mt-1 text-[#5d675f]">Existing: {conflict.existing}</p>
                    <p className="text-[#5d675f]">Imported: {conflict.imported}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* The resume "page" — a centered white document. */}
          <article className="rounded-xl border border-line bg-white p-6 shadow-sm md:p-10">
            <HeaderSection {...shared} />
            <SummarySection {...shared} />
            <SkillsSection {...shared} />
            <ExperienceSection {...shared} />
            <ProjectsSection {...shared} />
            <EducationSection {...shared} />
            <AwardsSection {...shared} />
            <CertificationsSection {...shared} />
            <TargetsSection {...shared} />
            <LinksSection {...shared} />
          </article>

          <section className="rounded-lg border border-line bg-white p-4">
            <button className="text-sm font-medium text-pine" type="button" onClick={() => setShowRawText((value) => !value)}>
              {showRawText ? "Hide raw extracted text" : "Show raw extracted text"}
            </button>
            {showRawText && (
              <p className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-panel p-3 text-sm text-[#5d675f]">
                {editableDraft.raw_text_preview || "No raw text preview available."}
              </p>
            )}
          </section>
        </div>
      </div>

      <div className="sticky bottom-0 z-10 border-t border-line bg-white/95 px-1 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-[880px] flex-wrap items-center gap-3">
          <Button type="button" disabled={saving} onClick={() => apply(allSections, "all")}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Accept all
          </Button>
          <Button variant="secondary" type="button" disabled={saving || selected.length === 0} onClick={() => apply(selected, "selected")}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Apply selected
          </Button>
          <Button variant="secondary" type="button" disabled={saving} onClick={onCancel}>
            <X className="h-4 w-4" /> Cancel
          </Button>
          <span className="text-xs text-[#5d675f]">{selected.length} of {allSections.length} sections selected</span>
        </div>
      </div>
    </div>
  );
}

type SharedProps = {
  draft: EditableImportDraft;
  setDraft: Dispatch<SetStateAction<EditableImportDraft>>;
  selected: ImportSection[];
  editing: ImportSection[];
  toggle: (section: ImportSection) => void;
  toggleEdit: (section: ImportSection) => void;
};

function ResumeSection({
  section,
  title,
  selected,
  editing,
  toggle,
  toggleEdit,
  onAdd,
  view,
  edit,
  isEmpty
}: {
  section: ImportSection;
  title: string;
  selected: ImportSection[];
  editing: ImportSection[];
  toggle: (section: ImportSection) => void;
  toggleEdit: (section: ImportSection) => void;
  onAdd?: () => void;
  view: ReactNode;
  edit: ReactNode;
  isEmpty?: boolean;
}) {
  const included = selected.includes(section);
  const isEditing = editing.includes(section);
  if (isEmpty && !isEditing) {
    // Do not clutter the resume with empty sections by default; the user can
    // still include/edit them from the controls.
    return null;
  }
  return (
    <section className={`border-t border-line/70 py-5 first:border-t-0 first:pt-0 ${included ? "" : "opacity-45"}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold uppercase tracking-wide text-[#324034]">{title}</h4>
        <div className="flex items-center gap-2">
          {onAdd && isEditing && (
            <button
              type="button"
              onClick={onAdd}
              className="focus-ring inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs font-medium"
            >
              <Plus className="h-3.5 w-3.5" /> Add
            </button>
          )}
          <button
            type="button"
            onClick={() => toggleEdit(section)}
            aria-label={`${isEditing ? "Done editing" : "Edit"} ${title}`}
            className="focus-ring inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs font-medium"
          >
            {isEditing ? <Check className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
            {isEditing ? "Done" : "Edit"}
          </button>
          <label className="inline-flex items-center gap-1.5 text-xs font-medium">
            <input type="checkbox" aria-label={`Include ${title}`} checked={included} onChange={() => toggle(section)} />
            Include
          </label>
        </div>
      </div>
      {isEditing ? edit : view}
    </section>
  );
}

function HeaderSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const basic = draft.basic_info;
  const linkedin = draft.links.linkedin_url || basic.linkedin_url;
  const github = draft.links.github_url || basic.github_url;
  const portfolio = draft.links.portfolio_url || basic.portfolio_url;
  const contactBits = [
    basic.email,
    basic.phone,
    [basic.location_city, basic.location_state].filter(Boolean).join(", "),
    linkedin,
    github,
    portfolio
  ].filter(Boolean);

  const view = (
    <div className="text-center">
      <h2 className="text-2xl font-bold tracking-tight text-[#1c261f]">{basic.full_name || "Your name"}</h2>
      {basic.headline && <p className="mt-1 text-sm font-medium text-pine">{basic.headline}</p>}
      {contactBits.length > 0 && (
        <p className="mt-2 flex flex-wrap justify-center gap-x-2 gap-y-1 text-xs text-[#5d675f]">
          {contactBits.map((bit, index) => (
            <span key={`${bit}-${index}`} className="break-all">
              {index > 0 && <span className="mr-2 text-line">·</span>}
              {bit}
            </span>
          ))}
        </p>
      )}
    </div>
  );

  const edit = (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="Full name" value={basic.full_name} onChange={(value) => updateBasic(setDraft, "full_name", value)} />
      <Field label="Headline" value={basic.headline} onChange={(value) => updateBasic(setDraft, "headline", value)} />
      <Field label="Email" value={basic.email} onChange={(value) => updateBasic(setDraft, "email", value)} />
      <Field label="Phone" value={basic.phone} onChange={(value) => updateBasic(setDraft, "phone", value)} />
      <Field label="City" value={basic.location_city} onChange={(value) => updateBasic(setDraft, "location_city", value)} />
      <Field label="State" value={basic.location_state} onChange={(value) => updateBasic(setDraft, "location_state", value)} />
      <Field label="Country" value={basic.location_country} onChange={(value) => updateBasic(setDraft, "location_country", value)} />
      <Field label="LinkedIn" value={linkedin} onChange={(value) => updateLink(setDraft, "linkedin_url", value)} />
      <Field label="GitHub" value={github} onChange={(value) => updateLink(setDraft, "github_url", value)} />
      <Field label="Portfolio" value={portfolio} onChange={(value) => updateLink(setDraft, "portfolio_url", value)} />
      <Field label="Work authorization" value={basic.work_authorization_status} onChange={(value) => updateBasic(setDraft, "work_authorization_status", value)} />
      <Toggle label="Requires sponsorship" checked={Boolean(basic.requires_sponsorship)} onChange={(value) => updateBasic(setDraft, "requires_sponsorship", value)} />
    </div>
  );

  return (
    <ResumeSection
      section="basic_info"
      title="Header"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      view={view}
      edit={edit}
    />
  );
}

function SummarySection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const view = <p className="text-sm leading-6 text-[#33403a]">{draft.summary}</p>;
  const edit = <TextArea label="Professional summary" value={draft.summary} onChange={(value) => setDraft((current) => ({ ...current, summary: value }))} />;
  return (
    <ResumeSection
      section="basic_info"
      title="Professional Summary"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      view={view}
      edit={edit}
      isEmpty={!draft.summary.trim()}
    />
  );
}

function SkillsSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const groups = draft.skill_groups.filter((group) => group.items.length > 0);
  const view =
    groups.length > 0 ? (
      <div className="grid gap-1.5 text-sm text-[#33403a]">
        {groups.map((group, index) => (
          <p key={`${group.category}-${index}`}>
            {group.category && <span className="font-semibold">{group.category}: </span>}
            {group.items.join(", ")}
          </p>
        ))}
      </div>
    ) : (
      <div className="flex flex-wrap gap-2">
        {draft.skills.map((skill) => (
          <span key={skill} className="rounded-full border border-line bg-panel px-3 py-1 text-xs">
            {skill}
          </span>
        ))}
      </div>
    );
  const edit = (
    <ChipEditor
      label="Skills"
      values={draft.skills}
      onChange={(value) => setDraft((current) => ({ ...current, skills: value, skill_groups: [] }))}
    />
  );
  return (
    <ResumeSection
      section="skills"
      title="Skills"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      view={view}
      edit={edit}
      isEmpty={draft.skills.length === 0 && groups.length === 0}
    />
  );
}

function ExperienceSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const empty: ExperienceDraft = { company: "", title: "", location: "", start_date: "", end_date: "", currently_working: false, bullets: [], technologies: [], measurable_impact: [] };
  const view = (
    <div className="grid gap-4">
      {draft.experience.map((item, index) => (
        <div key={index}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <p className="font-semibold text-[#1c261f]">{[item.title, item.company].filter(Boolean).join(" — ")}</p>
            <p className="text-xs text-[#5d675f]">{formatDateRange(item.start_date, item.end_date, item.currently_working)}</p>
          </div>
          {item.location && <p className="text-xs text-[#5d675f]">{item.location}</p>}
          <BulletList items={item.bullets} />
        </div>
      ))}
    </div>
  );
  return (
    <ResumeSection
      section="experience"
      title="Professional Experience"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      onAdd={() => setDraft((current) => ({ ...current, experience: [...current.experience, { ...empty }] }))}
      view={view}
      edit={
        <RecordEditor
          records={draft.experience}
          onChange={(experience) => setDraft((current) => ({ ...current, experience }))}
          label="Experience"
          render={(record, index, setRecord) => (
            <div className="grid gap-3 md:grid-cols-2">
              <Field label={`Experience ${index + 1} title`} value={record.title} onChange={(value) => setRecord({ ...record, title: value })} />
              <Field label={`Experience ${index + 1} company`} value={record.company} onChange={(value) => setRecord({ ...record, company: value })} />
              <Field label={`Experience ${index + 1} location`} value={record.location} onChange={(value) => setRecord({ ...record, location: value })} />
              <Toggle label={`Experience ${index + 1} current role`} checked={record.currently_working} onChange={(value) => setRecord({ ...record, currently_working: value })} />
              <Field label={`Experience ${index + 1} start date`} value={record.start_date} onChange={(value) => setRecord({ ...record, start_date: value })} />
              <Field label={`Experience ${index + 1} end date`} value={record.end_date} onChange={(value) => setRecord({ ...record, end_date: value })} />
              <div className="md:col-span-2">
                <TextList label={`Experience ${index + 1} bullets`} values={record.bullets} onChange={(value) => setRecord({ ...record, bullets: value })} />
              </div>
              <ChipEditor label={`Experience ${index + 1} technologies`} values={record.technologies} onChange={(value) => setRecord({ ...record, technologies: value })} />
            </div>
          )}
        />
      }
      isEmpty={draft.experience.length === 0}
    />
  );
}

function ProjectsSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const empty: ProjectDraft = { name: "", subtitle: "", description: "", bullets: [], technologies: [], links: [], start_date: "", end_date: "" };
  const view = (
    <div className="grid gap-4">
      {draft.projects.map((item, index) => (
        <div key={index}>
          <p className="font-semibold text-[#1c261f]">{item.name}</p>
          {item.subtitle && <p className="text-xs italic text-[#5d675f]">{item.subtitle}</p>}
          {item.description && <p className="mt-1 text-sm text-[#33403a]">{item.description}</p>}
          <BulletList items={item.bullets} />
        </div>
      ))}
    </div>
  );
  return (
    <ResumeSection
      section="projects"
      title="Selected Projects"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      onAdd={() => setDraft((current) => ({ ...current, projects: [...current.projects, { ...empty }] }))}
      view={view}
      edit={
        <RecordEditor
          records={draft.projects}
          onChange={(projects) => setDraft((current) => ({ ...current, projects }))}
          label="Project"
          render={(record, index, setRecord) => (
            <div className="grid gap-3 md:grid-cols-2">
              <Field label={`Project ${index + 1} name`} value={record.name} onChange={(value) => setRecord({ ...record, name: value })} />
              <Field label={`Project ${index + 1} subtitle`} value={record.subtitle} onChange={(value) => setRecord({ ...record, subtitle: value })} />
              <div className="md:col-span-2">
                <TextList label={`Project ${index + 1} bullets`} values={record.bullets} onChange={(value) => setRecord({ ...record, bullets: value })} />
              </div>
              <ChipEditor label={`Project ${index + 1} technologies`} values={record.technologies} onChange={(value) => setRecord({ ...record, technologies: value })} />
              <ChipEditor label={`Project ${index + 1} links`} values={record.links} onChange={(value) => setRecord({ ...record, links: value })} />
            </div>
          )}
        />
      }
      isEmpty={draft.projects.length === 0}
    />
  );
}

function EducationSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const empty: EducationDraft = { school: "", degree: "", major: "", minor: "", start_date: "", end_date: "", gpa: "", honors: [], coursework: [] };
  const view = (
    <div className="grid gap-4">
      {draft.education.map((item, index) => (
        <div key={index}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <p className="font-semibold text-[#1c261f]">{item.school}</p>
            <p className="text-xs text-[#5d675f]">{formatDateRange(item.start_date, item.end_date, false)}</p>
          </div>
          <p className="text-sm text-[#33403a]">{[item.degree, item.minor && `Minor in ${item.minor}`].filter(Boolean).join(", ")}</p>
          {item.gpa && <p className="text-xs text-[#5d675f]">GPA: {item.gpa}</p>}
          {item.honors.length > 0 && <p className="text-xs text-[#5d675f]">Honors: {item.honors.join(", ")}</p>}
        </div>
      ))}
    </div>
  );
  return (
    <ResumeSection
      section="education"
      title="Education"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      onAdd={() => setDraft((current) => ({ ...current, education: [...current.education, { ...empty }] }))}
      view={view}
      edit={
        <RecordEditor
          records={draft.education}
          onChange={(education) => setDraft((current) => ({ ...current, education }))}
          label="Education"
          render={(record, index, setRecord) => (
            <div className="grid gap-3 md:grid-cols-2">
              <Field label={`Education ${index + 1} school`} value={record.school} onChange={(value) => setRecord({ ...record, school: value })} />
              <Field label={`Education ${index + 1} degree`} value={record.degree} onChange={(value) => setRecord({ ...record, degree: value })} />
              <Field label={`Education ${index + 1} major`} value={record.major} onChange={(value) => setRecord({ ...record, major: value })} />
              <Field label={`Education ${index + 1} minor`} value={record.minor} onChange={(value) => setRecord({ ...record, minor: value })} />
              <Field label={`Education ${index + 1} start date`} value={record.start_date} onChange={(value) => setRecord({ ...record, start_date: value })} />
              <Field label={`Education ${index + 1} end date`} value={record.end_date} onChange={(value) => setRecord({ ...record, end_date: value })} />
              <Field label={`Education ${index + 1} GPA`} value={record.gpa} onChange={(value) => setRecord({ ...record, gpa: value })} />
              <ChipEditor label={`Education ${index + 1} honors`} values={record.honors} onChange={(value) => setRecord({ ...record, honors: value })} />
            </div>
          )}
        />
      }
      isEmpty={draft.education.length === 0}
    />
  );
}

function AwardsSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const empty: AwardDraft = { name: "", issuer: "", date: "", description: "" };
  const view = (
    <ul className="grid list-disc gap-1 pl-5 text-sm text-[#33403a]">
      {draft.awards.map((item, index) => (
        <li key={index}>
          {item.name}
          {item.date && <span className="text-[#5d675f]"> ({item.date})</span>}
        </li>
      ))}
    </ul>
  );
  return (
    <ResumeSection
      section="awards"
      title="Awards, Publications & Recognition"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      onAdd={() => setDraft((current) => ({ ...current, awards: [...current.awards, { ...empty }] }))}
      view={view}
      edit={
        <RecordEditor
          records={draft.awards}
          onChange={(awards) => setDraft((current) => ({ ...current, awards }))}
          label="Award"
          render={(record, index, setRecord) => (
            <div className="grid gap-3 md:grid-cols-2">
              <Field label={`Award ${index + 1} name`} value={record.name} onChange={(value) => setRecord({ ...record, name: value })} />
              <Field label={`Award ${index + 1} issuer`} value={record.issuer} onChange={(value) => setRecord({ ...record, issuer: value })} />
              <Field label={`Award ${index + 1} date`} value={record.date} onChange={(value) => setRecord({ ...record, date: value })} />
              <div className="md:col-span-2">
                <TextArea label={`Award ${index + 1} description`} value={record.description} onChange={(value) => setRecord({ ...record, description: value })} />
              </div>
            </div>
          )}
        />
      }
      isEmpty={draft.awards.length === 0}
    />
  );
}

function CertificationsSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const empty: CertificationDraft = { name: "", issuer: "", issue_date: "", expiration_date: "", credential_url: "" };
  const view = (
    <ul className="grid list-disc gap-1 pl-5 text-sm text-[#33403a]">
      {draft.certifications.map((item, index) => (
        <li key={index}>{[item.name, item.issuer].filter(Boolean).join(" — ")}</li>
      ))}
    </ul>
  );
  return (
    <ResumeSection
      section="certifications"
      title="Certifications"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      onAdd={() => setDraft((current) => ({ ...current, certifications: [...current.certifications, { ...empty }] }))}
      view={view}
      edit={
        <RecordEditor
          records={draft.certifications}
          onChange={(certifications) => setDraft((current) => ({ ...current, certifications }))}
          label="Certification"
          render={(record, index, setRecord) => (
            <div className="grid gap-3 md:grid-cols-2">
              <Field label={`Certification ${index + 1} name`} value={record.name} onChange={(value) => setRecord({ ...record, name: value })} />
              <Field label={`Certification ${index + 1} issuer`} value={record.issuer} onChange={(value) => setRecord({ ...record, issuer: value })} />
              <Field label={`Certification ${index + 1} credential URL`} value={record.credential_url} onChange={(value) => setRecord({ ...record, credential_url: value })} />
            </div>
          )}
        />
      }
      isEmpty={draft.certifications.length === 0}
    />
  );
}

function TargetsSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const targets = draft.job_targets;
  const view = (
    <div className="grid gap-1 text-sm text-[#33403a]">
      {targets.target_roles.length > 0 && <p><span className="font-semibold">Roles: </span>{targets.target_roles.join(", ")}</p>}
      {targets.target_levels.length > 0 && <p><span className="font-semibold">Levels: </span>{targets.target_levels.join(", ")}</p>}
      {targets.preferred_locations.length > 0 && <p><span className="font-semibold">Locations: </span>{targets.preferred_locations.join(", ")}</p>}
      {targets.work_preference && <p><span className="font-semibold">Preference: </span>{targets.work_preference}</p>}
    </div>
  );
  const edit = (
    <div className="grid gap-4 md:grid-cols-2">
      <ChipEditor label="Target roles" values={targets.target_roles} onChange={(value) => updateTargets(setDraft, "target_roles", value)} />
      <ChipEditor label="Target levels" values={targets.target_levels} onChange={(value) => updateTargets(setDraft, "target_levels", value)} />
      <ChipEditor label="Preferred locations" values={targets.preferred_locations} onChange={(value) => updateTargets(setDraft, "preferred_locations", value)} />
      <label>
        <span className="text-sm font-medium">Preference</span>
        <select
          className="mt-2 h-10 w-full rounded-md border border-line bg-white px-3"
          value={targets.work_preference || "everything"}
          onChange={(event) => updateTargets(setDraft, "work_preference", event.target.value)}
        >
          <option value="everything">Everything</option>
          <option value="remote">Remote</option>
          <option value="hybrid">Hybrid</option>
          <option value="onsite">Onsite</option>
        </select>
      </label>
    </div>
  );
  const empty =
    targets.target_roles.length === 0 &&
    targets.target_levels.length === 0 &&
    targets.preferred_locations.length === 0 &&
    !targets.work_preference;
  return (
    <ResumeSection
      section="job_targets"
      title="Job Targets"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      view={view}
      edit={edit}
      isEmpty={empty}
    />
  );
}

function LinksSection({ draft, setDraft, selected, editing, toggle, toggleEdit }: SharedProps) {
  const links = draft.links;
  const all = [links.linkedin_url, links.github_url, links.portfolio_url, ...links.other_links].filter(Boolean);
  const view = (
    <ul className="grid list-disc gap-1 pl-5 text-sm text-pine">
      {all.map((link) => (
        <li key={link} className="break-all">{link}</li>
      ))}
    </ul>
  );
  const edit = (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="LinkedIn" value={links.linkedin_url} onChange={(value) => updateLink(setDraft, "linkedin_url", value)} />
      <Field label="GitHub" value={links.github_url} onChange={(value) => updateLink(setDraft, "github_url", value)} />
      <Field label="Portfolio" value={links.portfolio_url} onChange={(value) => updateLink(setDraft, "portfolio_url", value)} />
      <ChipEditor label="Other links" values={links.other_links} onChange={(value) => setDraft((current) => ({ ...current, links: { ...current.links, other_links: value } }))} />
    </div>
  );
  return (
    <ResumeSection
      section="links"
      title="Links"
      selected={selected}
      editing={editing}
      toggle={toggle}
      toggleEdit={toggleEdit}
      view={view}
      edit={edit}
      isEmpty={all.length === 0}
    />
  );
}

function RecordEditor<T>({
  records,
  onChange,
  label,
  render
}: {
  records: T[];
  onChange: (records: T[]) => void;
  label: string;
  render: (record: T, index: number, setRecord: (record: T) => void) => ReactNode;
}) {
  if (records.length === 0) {
    return <p className="rounded-md border border-dashed border-line bg-panel p-4 text-center text-sm text-[#5d675f]">No records. Use “Add” to create one.</p>;
  }
  return (
    <div className="grid gap-4">
      {records.map((record, index) => (
        <div key={index} className="rounded-md border border-line bg-panel/50 p-4">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-sm font-semibold">{label} {index + 1}</span>
            <Button variant="danger" type="button" onClick={() => onChange(records.filter((_, itemIndex) => itemIndex !== index))}>
              <Trash2 className="h-4 w-4" /> Remove
            </Button>
          </div>
          {render(record, index, (next) => onChange(records.map((item, itemIndex) => (itemIndex === index ? next : item))))}
        </div>
      ))}
    </div>
  );
}

function BulletList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <ul className="mt-1 grid list-disc gap-1 pl-5 text-sm leading-6 text-[#33403a]">
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

function formatDateRange(start: string, end: string, current: boolean) {
  const finish = current ? "Present" : end;
  return [start, finish].filter(Boolean).join(" – ");
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label>
      <span className="text-sm font-medium">{label}</span>
      <input className="mt-2 h-10 w-full rounded-md border border-line px-3" type="text" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function TextArea({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label>
      <span className="text-sm font-medium">{label}</span>
      <textarea className="mt-2 min-h-24 w-full rounded-md border border-line p-3" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex min-h-10 items-center gap-3 rounded-md border border-line p-3">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="text-sm font-medium">{label}</span>
    </label>
  );
}

function ChipEditor({ label, values, onChange }: { label: string; values: string[]; onChange: (values: string[]) => void }) {
  const [input, setInput] = useState("");

  function add() {
    const next = uniqueStrings([...values, ...splitValues(input)]);
    onChange(next);
    setInput("");
  }

  return (
    <div>
      <label>
        <span className="text-sm font-medium">{label}</span>
        <div className="mt-2 flex gap-2">
          <input
            className="h-10 min-w-0 flex-1 rounded-md border border-line px-3"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
          />
          <Button variant="secondary" type="button" onClick={add}>
            <Plus className="h-4 w-4" /> Add
          </Button>
        </div>
      </label>
      {values.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {values.map((value) => (
            <span key={value} className="inline-flex max-w-full items-center gap-2 rounded-full border border-line bg-panel px-3 py-1 text-sm">
              <span className="break-all">{value}</span>
              <button type="button" onClick={() => onChange(values.filter((item) => item !== value))} aria-label={`Remove ${value}`}>
                <X className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function TextList({ label, values, onChange }: { label: string; values: string[]; onChange: (values: string[]) => void }) {
  return <TextArea label={label} value={values.join("\n")} onChange={(value) => onChange(value.split("\n").map((item) => item.trim()).filter(Boolean))} />;
}

function normalizeImportDraft(draft: Record<string, unknown>): EditableImportDraft {
  const basic = recordValue(draft.basic_info);
  const targets = recordValue(draft.job_targets);
  const links = recordValue(draft.links);
  return {
    basic_info: {
      full_name: stringValue(basic.full_name),
      headline: stringValue(basic.headline),
      email: stringValue(basic.email),
      phone: stringValue(basic.phone),
      location_city: stringValue(basic.location_city),
      location_state: stringValue(basic.location_state),
      location_country: stringValue(basic.location_country),
      linkedin_url: stringValue(basic.linkedin_url),
      github_url: stringValue(basic.github_url),
      portfolio_url: stringValue(basic.portfolio_url),
      work_authorization_status: stringValue(basic.work_authorization_status ?? basic.work_authorization),
      requires_sponsorship: typeof basic.requires_sponsorship === "boolean" ? basic.requires_sponsorship : null
    },
    summary: stringValue(draft.summary ?? basic.summary),
    job_targets: {
      target_roles: stringList(targets.target_roles),
      target_levels: stringList(targets.target_levels),
      preferred_locations: stringList(targets.preferred_locations),
      work_preference: stringValue(targets.work_preference || "everything")
    },
    education: arrayValue(draft.education).map((item) => normalizeEducation(recordValue(item))),
    experience: arrayValue(draft.experience).map((item) => normalizeExperience(recordValue(item))),
    projects: arrayValue(draft.projects).map((item) => normalizeProject(recordValue(item))),
    skills: stringList(draft.skills),
    skill_groups: arrayValue(draft.skill_groups).map((item) => normalizeSkillGroup(recordValue(item))).filter((group) => group.items.length > 0),
    certifications: arrayValue(draft.certifications).map((item) => normalizeCertification(recordValue(item))),
    awards: arrayValue(draft.awards).map((item) => normalizeAward(recordValue(item))),
    links: {
      linkedin_url: stringValue(links.linkedin_url ?? basic.linkedin_url),
      github_url: stringValue(links.github_url ?? basic.github_url),
      portfolio_url: stringValue(links.portfolio_url ?? basic.portfolio_url),
      other_links: stringList(links.other_links)
    },
    raw_text_preview: stringValue(draft.raw_text_preview),
    confidence_warnings: uniqueStrings(stringList(draft.confidence_warnings)),
    missing_fields: uniqueStrings(stringList(draft.missing_fields)),
    source_type: stringValue(draft.source_type || "unknown"),
    low_confidence_fields: uniqueStrings(stringList(draft.low_confidence_fields))
  };
}

function normalizeEducation(item: Record<string, unknown>): EducationDraft {
  return {
    school: stringValue(item.school),
    degree: stringValue(item.degree),
    major: stringValue(item.major),
    minor: stringValue(item.minor),
    start_date: stringValue(item.start_date),
    end_date: stringValue(item.end_date),
    gpa: stringValue(item.gpa),
    honors: stringList(item.honors),
    coursework: stringList(item.coursework)
  };
}

function normalizeExperience(item: Record<string, unknown>): ExperienceDraft {
  return {
    company: stringValue(item.company),
    title: stringValue(item.title),
    location: stringValue(item.location),
    start_date: stringValue(item.start_date),
    end_date: stringValue(item.end_date),
    currently_working: Boolean(item.currently_working),
    bullets: stringList(item.bullets),
    technologies: stringList(item.technologies),
    measurable_impact: stringList(item.measurable_impact)
  };
}

function normalizeProject(item: Record<string, unknown>): ProjectDraft {
  return {
    name: stringValue(item.name),
    subtitle: stringValue(item.subtitle),
    description: stringValue(item.description),
    bullets: stringList(item.bullets),
    technologies: stringList(item.technologies),
    links: stringList(item.links),
    start_date: stringValue(item.start_date),
    end_date: stringValue(item.end_date)
  };
}

function normalizeCertification(item: Record<string, unknown>): CertificationDraft {
  return {
    name: stringValue(item.name),
    issuer: stringValue(item.issuer),
    issue_date: stringValue(item.issue_date),
    expiration_date: stringValue(item.expiration_date),
    credential_url: stringValue(item.credential_url)
  };
}

function normalizeAward(item: Record<string, unknown>): AwardDraft {
  return {
    name: stringValue(item.name),
    issuer: stringValue(item.issuer),
    date: stringValue(item.date),
    description: stringValue(item.description)
  };
}

function normalizeSkillGroup(item: Record<string, unknown>): SkillGroupDraft {
  return {
    category: stringValue(item.category),
    items: stringList(item.items)
  };
}

function updateBasic<K extends keyof BasicInfoDraft>(
  setDraft: Dispatch<SetStateAction<EditableImportDraft>>,
  key: K,
  value: BasicInfoDraft[K]
) {
  setDraft((current) => ({ ...current, basic_info: { ...current.basic_info, [key]: value } }));
}

function updateTargets<K extends keyof JobTargetsDraft>(
  setDraft: Dispatch<SetStateAction<EditableImportDraft>>,
  key: K,
  value: JobTargetsDraft[K]
) {
  setDraft((current) => ({ ...current, job_targets: { ...current.job_targets, [key]: value } }));
}

type PrimaryLinkField = "linkedin_url" | "github_url" | "portfolio_url";

function updateLink(setDraft: Dispatch<SetStateAction<EditableImportDraft>>, key: PrimaryLinkField, value: string) {
  setDraft((current) => ({
    ...current,
    links: { ...current.links, [key]: value },
    basic_info: { ...current.basic_info, [key]: value }
  }));
}

function findConflicts(draft: EditableImportDraft, currentProfileObject: object, currentCareerObject: object) {
  const currentProfile = currentProfileObject as Record<string, unknown>;
  const currentCareer = currentCareerObject as Record<string, unknown>;
  const conflicts: { field: string; existing: string; imported: string }[] = [];
  const fields: [string, string][] = [
    ["full_name", draft.basic_info.full_name],
    ["phone", draft.basic_info.phone],
    ["location_city", draft.basic_info.location_city],
    ["location_state", draft.basic_info.location_state],
    ["location_country", draft.basic_info.location_country],
    ["linkedin_url", draft.links.linkedin_url || draft.basic_info.linkedin_url],
    ["github_url", draft.links.github_url || draft.basic_info.github_url],
    ["portfolio_url", draft.links.portfolio_url || draft.basic_info.portfolio_url]
  ];

  for (const [field, imported] of fields) {
    const existing = currentProfile[field];
    if (hasConflict(existing, imported)) {
      conflicts.push({ field, existing: String(existing), imported });
    }
  }

  const listFields: [string, string[]][] = [
    ["target_roles", draft.job_targets.target_roles],
    ["target_levels", draft.job_targets.target_levels],
    ["preferred_locations", draft.job_targets.preferred_locations],
    ["skills", draft.skills]
  ];
  for (const [field, imported] of listFields) {
    const existing = stringList(currentProfile[field]);
    if (existing.length > 0 && imported.length > 0 && existing.join(", ") !== imported.join(", ")) {
      conflicts.push({ field, existing: existing.join(", "), imported: imported.join(", ") });
    }
  }

  for (const section of ["education", "experience", "projects", "certifications", "awards"] as const) {
    const existingCount = arrayValue(currentCareer[section]).length;
    const importedCount = draft[section].length;
    if (existingCount > 0 && importedCount > 0) {
      conflicts.push({
        field: section,
        existing: `${existingCount} saved record(s)`,
        imported: `${importedCount} imported record(s)`
      });
    }
  }
  return conflicts;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
}

function stringList(value: unknown): string[] {
  if (typeof value === "string") {
    return splitValues(value);
  }
  if (Array.isArray(value)) {
    return uniqueStrings(value.map(stringValue).filter(Boolean));
  }
  return [];
}

function splitValues(value: string) {
  return value.replace(/;/g, ",").split(",").map((item) => item.trim()).filter(Boolean);
}

function uniqueStrings(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function hasConflict(existing: unknown, imported: string) {
  return typeof existing === "string" && existing.trim().length > 0 && imported.trim().length > 0 && existing.trim() !== imported.trim();
}
