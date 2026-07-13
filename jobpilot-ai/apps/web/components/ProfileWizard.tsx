"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ClipboardPaste,
  Loader2,
  Plus,
  Save,
  Trash2,
  Upload,
  X
} from "lucide-react";
import { Button } from "@/components/Button";
import {
  ImportProfilePreview,
  type EditableImportDraft,
  type ImportApplyMode,
  type ImportSection
} from "@/components/ImportProfilePreview";
import { api } from "@/lib/api";

const steps = [
  "Import",
  "Basic info",
  "Job targets",
  "Education",
  "Experience",
  "Projects",
  "Skills",
  "Links",
  "Optional EEO",
  "Review"
];

const workAuthorizationOptions = [
  ["authorized_us", "Authorized to work in the United States"],
  ["authorized_other_country", "Authorized to work in another country"],
  ["need_sponsorship_now", "Need sponsorship now"],
  ["need_sponsorship_future", "Need sponsorship in the future"],
  ["student_visa", "Student visa / OPT / CPT"],
  ["opt_cpt", "OPT / CPT"],
  ["not_authorized", "Not currently authorized"],
  ["prefer_not_to_say", "Prefer not to say"],
  ["other", "Other"]
] as const;

const sponsorshipSuggested = new Set([
  "need_sponsorship_now",
  "need_sponsorship_future",
  "student_visa",
  "opt_cpt"
]);

const roleGroups = [
  {
    label: "Software & Engineering",
    options: [
      "Software Engineer",
      "Backend Engineer",
      "Frontend Engineer",
      "Full Stack Engineer",
      "Mobile Developer",
      "iOS Developer",
      "Android Developer",
      "DevOps Engineer",
      "Site Reliability Engineer",
      "Cloud Engineer",
      "Platform Engineer",
      "QA Engineer",
      "Automation Engineer",
      "Security Engineer",
      "Embedded Software Engineer",
      "Firmware Engineer",
      "Systems Engineer"
    ]
  },
  {
    label: "AI/Data",
    options: [
      "AI Engineer",
      "Machine Learning Engineer",
      "MLOps Engineer",
      "Data Scientist",
      "Data Analyst",
      "Data Engineer",
      "Business Intelligence Analyst",
      "Research Engineer",
      "NLP Engineer",
      "Computer Vision Engineer",
      "Applied Scientist"
    ]
  },
  {
    label: "Product/Design",
    options: [
      "Product Manager",
      "Associate Product Manager",
      "Product Analyst",
      "UX Designer",
      "UI Designer",
      "UX Researcher"
    ]
  },
  {
    label: "Business/Operations",
    options: [
      "Business Analyst",
      "Operations Analyst",
      "Strategy Analyst",
      "Project Coordinator",
      "Program Manager",
      "Technical Program Manager",
      "Customer Success Manager",
      "Sales Development Representative",
      "Marketing Analyst",
      "Growth Analyst"
    ]
  },
  {
    label: "Mechanical/Hardware",
    options: [
      "Mechanical Engineer",
      "Manufacturing Engineer",
      "Industrial Engineer",
      "Electrical Engineer",
      "Hardware Engineer",
      "Robotics Engineer",
      "CAD Designer"
    ]
  }
];

const targetLevelOptions = [
  "Internship",
  "Co-op",
  "New Grad",
  "Entry Level",
  "Junior",
  "Associate",
  "Mid Level",
  "Senior",
  "Staff",
  "Principal",
  "Manager",
  "Director",
  "0-1 years",
  "1-3 years",
  "3-5 years",
  "5-10 years",
  "10+ years"
];

const locationQuickOptions = [
  "Remote",
  "United States",
  "Phoenix, AZ",
  "Tempe, AZ",
  "San Francisco, CA",
  "San Jose, CA",
  "Seattle, WA",
  "New York, NY",
  "Austin, TX",
  "Dallas, TX",
  "Chicago, IL",
  "Boston, MA",
  "Atlanta, GA",
  "Los Angeles, CA",
  "Washington, DC"
];

const skillSuggestions: Record<string, string[]> = {
  "Software Engineer": ["Python", "JavaScript", "TypeScript", "React", "Node.js", "SQL", "Git"],
  "Backend Engineer": ["Python", "FastAPI", "Django", "Flask", "PostgreSQL", "Redis", "Docker"],
  "AI Engineer": ["Python", "PyTorch", "TensorFlow", "LangChain", "OpenAI API", "RAG", "Vector Databases"],
  "Data Analyst": ["SQL", "Excel", "Tableau", "Power BI", "Python", "Pandas"]
};

type ProfileForm = {
  full_name: string;
  phone: string;
  location_city: string;
  location_state: string;
  location_country: string;
  work_authorization: string;
  requires_sponsorship: boolean;
  open_to_relocation: boolean;
  target_roles: string[];
  target_levels: string[];
  preferred_locations: string[];
  remote_preference: "everything" | "remote" | "hybrid" | "onsite";
  skills: string[];
  linkedin_url: string;
  github_url: string;
  portfolio_url: string;
};

type EducationRecord = {
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

type ExperienceRecord = {
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

type ProjectRecord = {
  name: string;
  description: string;
  bullets: string[];
  technologies: string[];
  links: string[];
  start_date: string;
  end_date: string;
};

type CareerForm = {
  education: EducationRecord[];
  experience: ExperienceRecord[];
  projects: ProjectRecord[];
  certifications: unknown[];
  awards: unknown[];
};

type ImportApplyResponse = {
  profile: (Partial<ProfileForm> & {
    work_authorization_status?: string | null;
    work_authorization?: string | null;
    work_preference?: ProfileForm["remote_preference"] | null;
    remote_preference?: ProfileForm["remote_preference"] | null;
  }) | null;
  career: Partial<CareerForm>;
};

type ImportDraft = {
  basic_info?: Partial<ProfileForm> & Record<string, unknown>;
  job_targets?: {
    target_roles?: string[];
    target_levels?: string[];
    preferred_locations?: string[];
    work_preference?: ProfileForm["remote_preference"];
  };
  education?: Partial<EducationRecord>[];
  experience?: Partial<ExperienceRecord>[];
  projects?: Partial<ProjectRecord>[];
  skills?: string[];
  links?: Partial<Pick<ProfileForm, "linkedin_url" | "github_url" | "portfolio_url">>;
  certifications?: unknown[];
  awards?: unknown[];
  raw_text_preview?: string;
  confidence_warnings?: string[];
  missing_fields?: string[];
  source_type?: string;
  low_confidence_fields?: string[];
};

const emptyProfile: ProfileForm = {
  full_name: "",
  phone: "",
  location_city: "",
  location_state: "",
  location_country: "United States",
  work_authorization: "prefer_not_to_say",
  requires_sponsorship: false,
  open_to_relocation: false,
  target_roles: [],
  target_levels: [],
  preferred_locations: [],
  remote_preference: "everything",
  skills: [],
  linkedin_url: "",
  github_url: "",
  portfolio_url: ""
};

const emptyCareer: CareerForm = {
  education: [],
  experience: [],
  projects: [],
  certifications: [],
  awards: []
};

export function ProfileWizard() {
  const [step, setStep] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [importOpen, setImportOpen] = useState(false);
  const [importDraft, setImportDraft] = useState<ImportDraft | null>(null);
  const [importLoading, setImportLoading] = useState("");
  const [importSaving, setImportSaving] = useState(false);
  const [importApplyError, setImportApplyError] = useState("");
  const [form, setForm] = useState<ProfileForm>(emptyProfile);
  const [career, setCareer] = useState<CareerForm>(emptyCareer);

  const suggestedSkills = useMemo(() => {
    const suggestions = form.target_roles.flatMap((role) => skillSuggestions[role] ?? []);
    return unique(suggestions).filter((skill) => !form.skills.includes(skill));
  }, [form.target_roles, form.skills]);

  useEffect(() => {
    let mounted = true;
    async function loadProfile() {
      setLoading(true);
      try {
        const [profileResult, careerResult] = await Promise.all([
          api<{ profile: (Partial<ProfileForm> & { work_preference?: ProfileForm["remote_preference"] }) | null }>("/profile"),
          api<Partial<CareerForm>>("/profile/career")
        ]);
        if (!mounted) {
          return;
        }
        if (profileResult.profile) {
          const profile = profileResult.profile;
          setForm({
            ...emptyProfile,
            ...profile,
            work_authorization: profile.work_authorization ?? "prefer_not_to_say",
            remote_preference: profile.work_preference ?? profile.remote_preference ?? "everything",
            target_roles: profile.target_roles ?? [],
            target_levels: profile.target_levels ?? [],
            preferred_locations: profile.preferred_locations ?? [],
            skills: profile.skills ?? []
          });
        }
        setCareer({
          education: normalizeEducationList(careerResult.education ?? []),
          experience: normalizeExperienceList(careerResult.experience ?? []),
          projects: normalizeProjectList(careerResult.projects ?? []),
          certifications: careerResult.certifications ?? [],
          awards: careerResult.awards ?? []
        });
      } catch (loadError) {
        if (mounted) {
          setError(loadError instanceof Error ? loadError.message : "Could not load profile.");
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    }
    loadProfile();
    return () => {
      mounted = false;
    };
  }, []);

  function update<K extends keyof ProfileForm>(key: K, value: ProfileForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateWorkAuthorization(value: string) {
    setForm((current) => ({
      ...current,
      work_authorization: value,
      requires_sponsorship: sponsorshipSuggested.has(value) ? true : current.requires_sponsorship
    }));
  }

  async function save() {
    setMessage("");
    setError("");
    if (!form.full_name.trim()) {
      setError("Full name is required.");
      setStep(1);
      return;
    }
    if (![form.linkedin_url, form.github_url, form.portfolio_url].every(isValidOptionalUrl)) {
      setError("Links must start with http:// or https://.");
      setStep(7);
      return;
    }
    setSaving(true);
    try {
      await api("/profile", {
        method: "PUT",
        body: JSON.stringify({
          ...form,
          work_authorization_status: form.work_authorization,
          work_preference: form.remote_preference,
          linkedin_url: form.linkedin_url || null,
          github_url: form.github_url || null,
          portfolio_url: form.portfolio_url || null
        })
      });
      await api("/profile/career", {
        method: "PUT",
        body: JSON.stringify({
          education: career.education.map(cleanEducation),
          experience: career.experience.map(cleanExperience),
          projects: career.projects.map(cleanProject),
          certifications: career.certifications,
          awards: career.awards
        })
      });
      setMessage("Profile saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save profile.");
    } finally {
      setSaving(false);
    }
  }

  async function importFile(file: File, sourceType: "resume" | "linkedin_pdf") {
    setError("");
    setMessage("");
    setImportApplyError("");
    setImportDraft(null);
    setImportOpen(true);
    setImportLoading(sourceType === "linkedin_pdf" ? "Parsing LinkedIn PDF..." : "Parsing resume...");
    const formData = new FormData();
    formData.append("source_type", sourceType);
    formData.append("file", file);
    try {
      const result = await api<{ draft: ImportDraft }>("/profile/import/file", {
        method: "POST",
        body: formData
      });
      setImportDraft(result.draft);
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "Could not import this file.");
    } finally {
      setImportLoading("");
    }
  }

  async function acceptImport(
    draft: EditableImportDraft,
    sections: ImportSection[] = [
      "basic_info",
      "job_targets",
      "education",
      "experience",
      "projects",
      "skills",
      "certifications",
      "awards",
      "links"
    ],
    overwriteConflicts = false,
    mode: ImportApplyMode = "all"
  ) {
    setImportSaving(true);
    setImportApplyError("");
    setMessage("");
    setError("");
    try {
      const result = await api<ImportApplyResponse>("/profile/import/apply", {
        method: "POST",
        body: JSON.stringify({ draft, sections, overwrite: overwriteConflicts })
      });
      if (result.profile) {
        setForm(formFromProfile(result.profile));
      }
      setCareer(careerFromResponse(result.career));
      setImportOpen(false);
      setImportDraft(null);
      setStep(9);
      setMessage(mode === "all" ? "Imported profile saved successfully." : "Selected imported sections saved successfully.");
    } catch (applyError) {
      setImportApplyError(applyError instanceof Error ? applyError.message : "Could not save imported profile.");
    } finally {
      setImportSaving(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
      <ol className="rounded-lg border border-line bg-white p-3">
        {steps.map((label, index) => (
          <li key={label}>
            <button
              className={`focus-ring w-full rounded-md px-3 py-2 text-left text-sm ${
                index === step ? "bg-panel font-medium text-pine" : "text-[#5d675f]"
              }`}
              onClick={() => setStep(index)}
              type="button"
            >
              {index + 1}. {label}
            </button>
          </li>
        ))}
      </ol>

      <section className="rounded-lg border border-line bg-white p-5">
        <div className="flex flex-col gap-3 border-b border-line pb-4 md:flex-row md:items-start md:justify-between">
          <div>
            <h2 className="text-xl font-semibold">{steps[step]}</h2>
            <p className="mt-1 text-sm text-[#5d675f]">
              {loading ? "Loading saved profile..." : "Update any section and save when ready."}
            </p>
          </div>
          <Button type="button" onClick={save} disabled={saving || loading}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save profile
          </Button>
        </div>

        {message && (
          <p className="mt-4 rounded-md border border-[#b9d7c3] bg-[#eef8f1] px-3 py-2 text-sm text-pine">{message}</p>
        )}
        {error && (
          <p className="mt-4 rounded-md border border-[#f0b4a4] bg-[#fff3ef] px-3 py-2 text-sm text-[#9f3d28]">{error}</p>
        )}

        <div className="mt-5">
          {step === 0 && (
            <ImportIntro
              loadingLabel={importLoading}
              onOpenPaste={() => {
                setImportDraft(null);
                setImportApplyError("");
                setImportOpen(true);
              }}
              onUpload={importFile}
            />
          )}

          {step === 1 && (
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Full name" value={form.full_name} required onChange={(value) => update("full_name", value)} />
              <Field label="Phone" value={form.phone} onChange={(value) => update("phone", value)} />
              <Field label="City" value={form.location_city} onChange={(value) => update("location_city", value)} />
              <Field label="State" value={form.location_state} onChange={(value) => update("location_state", value)} />
              <Field label="Country" value={form.location_country} onChange={(value) => update("location_country", value)} />
              <label>
                <span className="text-sm font-medium">Work authorization</span>
                <select
                  className="mt-2 h-10 w-full rounded-md border border-line bg-white px-3"
                  value={form.work_authorization}
                  onChange={(event) => updateWorkAuthorization(event.target.value)}
                >
                  {workAuthorizationOptions.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <Toggle
                label="Requires sponsorship"
                checked={form.requires_sponsorship}
                onChange={(value) => update("requires_sponsorship", value)}
              />
              <Toggle
                label="Open to relocation"
                checked={form.open_to_relocation}
                onChange={(value) => update("open_to_relocation", value)}
              />
            </div>
          )}

          {step === 2 && (
            <div className="grid gap-5">
              <MultiSelect
                label="Target roles"
                groups={roleGroups}
                selected={form.target_roles}
                onChange={(value) => update("target_roles", value)}
                allowCustom
                placeholder="Search roles or add a custom title"
              />
              <MultiSelect
                label="Target levels"
                groups={[{ label: "Level", options: targetLevelOptions }]}
                selected={form.target_levels}
                onChange={(value) => update("target_levels", value)}
                placeholder="Search levels"
              />
              <ChipInput
                label="Preferred locations"
                values={form.preferred_locations}
                quickOptions={locationQuickOptions}
                placeholder="Add city, state, country, or Remote"
                onChange={(value) => update("preferred_locations", value)}
              />
              <label>
                <span className="text-sm font-medium">Preference</span>
                <select
                  className="mt-2 h-10 w-full rounded-md border border-line bg-white px-3"
                  value={form.remote_preference}
                  onChange={(event) => update("remote_preference", event.target.value as ProfileForm["remote_preference"])}
                >
                  <option value="everything">Everything</option>
                  <option value="remote">Remote</option>
                  <option value="hybrid">Hybrid</option>
                  <option value="onsite">Onsite</option>
                </select>
              </label>
            </div>
          )}

          {step === 3 && (
            <EducationEditor
              records={career.education}
              onChange={(education) => setCareer((current) => ({ ...current, education }))}
            />
          )}

          {step === 4 && (
            <ExperienceEditor
              records={career.experience}
              onChange={(experience) => setCareer((current) => ({ ...current, experience }))}
            />
          )}

          {step === 5 && (
            <ProjectEditor
              records={career.projects}
              onChange={(projects) => setCareer((current) => ({ ...current, projects }))}
            />
          )}

          {step === 6 && (
            <div className="grid gap-4">
              <ChipInput
                label="Skills"
                values={form.skills}
                quickOptions={suggestedSkills}
                placeholder="Add skills separated by commas"
                onChange={(value) => update("skills", value)}
              />
            </div>
          )}

          {step === 7 && (
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="LinkedIn" value={form.linkedin_url} onChange={(value) => update("linkedin_url", value)} />
              <Field label="GitHub" value={form.github_url} onChange={(value) => update("github_url", value)} />
              <Field label="Portfolio" value={form.portfolio_url} onChange={(value) => update("portfolio_url", value)} />
            </div>
          )}

          {step === 8 && (
            <div className="rounded-md border border-line bg-panel p-4 text-sm leading-6 text-[#5d675f]">
              This information is optional. Prefer not to answer is always available. It is stored separately and is never used to rank jobs or generate resume content. You can delete it anytime from the EEO page.
            </div>
          )}

          {step === 9 && (
            <div className="grid gap-4">
              <ReviewBlock title="Profile" data={form} />
              <ReviewBlock title="Career" data={career} />
            </div>
          )}
        </div>

        <div className="mt-6 flex flex-wrap gap-3 border-t border-line pt-4">
          <Button variant="secondary" type="button" onClick={() => setStep(Math.max(0, step - 1))}>Back</Button>
          <Button variant="secondary" type="button" onClick={() => setStep(Math.min(steps.length - 1, step + 1))}>Next</Button>
        </div>
      </section>

      {importOpen && (
        <ImportProfileModal
          currentProfile={form}
          currentCareer={career}
          draft={importDraft}
          loadingLabel={importLoading}
          saving={importSaving}
          applyError={importApplyError}
          onDraft={setImportDraft}
          onClose={() => setImportOpen(false)}
          onAccept={(draft, sections, overwriteConflicts, mode) => acceptImport(draft, sections, overwriteConflicts, mode)}
          onError={setError}
        />
      )}
    </div>
  );
}

function ImportIntro({
  onOpenPaste,
  onUpload,
  loadingLabel
}: {
  onOpenPaste: () => void;
  onUpload: (file: File, sourceType: "resume" | "linkedin_pdf") => void;
  loadingLabel: string;
}) {
  return (
    <div className="grid gap-4">
      <div className="rounded-lg border border-line bg-panel p-5">
        <h3 className="text-lg font-semibold">Import your profile faster</h3>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[#5d675f]">
          Upload a resume, upload your LinkedIn PDF, or paste your profile text. JobPilot AI will extract a draft profile that you can review and edit.
        </p>
        <p className="mt-3 rounded-md border border-line bg-white px-3 py-2 text-sm font-medium">
          We do not log into LinkedIn or scrape your account. You control what you upload or paste.
        </p>
        <p className="mt-3 text-sm leading-6 text-[#5d675f]">
          On LinkedIn, open your profile, choose More, then Save to PDF. Upload that file here.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <FileUploadButton
            label="Upload resume"
            accept=".pdf,.docx"
            disabled={Boolean(loadingLabel)}
            onFile={(file) => onUpload(file, "resume")}
          />
          <FileUploadButton
            label="Upload LinkedIn PDF"
            accept=".pdf"
            disabled={Boolean(loadingLabel)}
            onFile={(file) => onUpload(file, "linkedin_pdf")}
          />
          <Button type="button" onClick={onOpenPaste}>
            <ClipboardPaste className="h-4 w-4" /> Paste profile text
          </Button>
        </div>
        {loadingLabel && <p className="mt-3 text-sm font-medium text-pine">{loadingLabel}</p>}
      </div>
    </div>
  );
}

function FileUploadButton({
  label,
  accept,
  disabled,
  onFile
}: {
  label: string;
  accept: string;
  disabled: boolean;
  onFile: (file: File) => void;
}) {
  return (
    <label className="focus-ring inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-md border border-line bg-white px-4 text-sm font-medium text-ink hover:bg-panel">
      <Upload className="h-4 w-4" /> {label}
      <input
        className="sr-only"
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            onFile(file);
          }
          event.currentTarget.value = "";
        }}
      />
    </label>
  );
}

function ImportProfileModal({
  currentProfile,
  currentCareer,
  draft,
  loadingLabel,
  saving,
  applyError,
  onDraft,
  onClose,
  onAccept,
  onError
}: {
  currentProfile: ProfileForm;
  currentCareer: CareerForm;
  draft: ImportDraft | null;
  loadingLabel: string;
  saving: boolean;
  applyError: string;
  onDraft: (draft: ImportDraft | null) => void;
  onClose: () => void;
  onAccept: (
    draft: EditableImportDraft,
    sections: ImportSection[],
    overwriteConflicts: boolean,
    mode: ImportApplyMode
  ) => void;
  onError: (message: string) => void;
}) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);

  async function extract() {
    setLoading(true);
    onError("");
    try {
      const result = await api<{ draft: ImportDraft }>("/profile/import/text", {
        method: "POST",
        body: JSON.stringify({ text, source_type: "resume_text" })
      });
      onDraft(result.draft);
    } catch (importError) {
      onError(importError instanceof Error ? importError.message : "Could not import profile text.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 p-4">
      <div className="mx-auto mt-4 flex max-h-[92vh] max-w-[1100px] flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-line bg-white px-5 py-3">
          <p className="text-xs text-[#5d675f]">
            We do not log into LinkedIn or scrape your account. You control what you upload or paste.
          </p>
          <button className="focus-ring rounded-md p-1.5" type="button" onClick={onClose} aria-label="Close import modal">
            <X className="h-5 w-5" />
          </button>
        </div>
        {applyError && (
          <p className="mx-5 mt-4 rounded-md border border-[#f0b4a4] bg-[#fff3ef] px-3 py-2 text-sm text-[#9f3d28]">
            {applyError}
          </p>
        )}
        {!draft ? (
          <div className="overflow-auto p-5">
            <h3 className="text-xl font-semibold">Paste profile text</h3>
            <p className="mb-3 mt-1 text-sm text-[#5d675f]">
              Paste your resume or LinkedIn “Save to PDF” text and we will extract a reviewable draft.
            </p>
            <textarea
              className="min-h-48 w-full rounded-md border border-line p-3 text-sm"
              placeholder="Paste resume text, LinkedIn Save to PDF text, or profile notes."
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
            <div className="mt-3 flex flex-wrap gap-3">
              <Button type="button" onClick={extract} disabled={loading || text.trim().length < 20}>
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ClipboardPaste className="h-4 w-4" />}
                Extract draft profile
              </Button>
              {loadingLabel && <p className="self-center text-sm font-medium text-pine">{loadingLabel}</p>}
            </div>
          </div>
        ) : (
          <div className="min-h-0 flex-1 px-5">
            <ImportProfilePreview
              draft={draft as Record<string, unknown>}
              currentProfile={currentProfile}
              currentCareer={currentCareer}
              saving={saving}
              onApply={(editedDraft, sections, overwriteConflicts, mode) => onAccept(editedDraft, sections, overwriteConflicts, mode)}
              onCancel={onClose}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function MultiSelect({
  label,
  groups,
  selected,
  onChange,
  placeholder,
  allowCustom = false
}: {
  label: string;
  groups: { label: string; options: string[] }[];
  selected: string[];
  onChange: (value: string[]) => void;
  placeholder: string;
  allowCustom?: boolean;
}) {
  const [query, setQuery] = useState("");
  const visibleGroups = groups
    .map((group) => ({
      ...group,
      options: group.options.filter((option) => option.toLowerCase().includes(query.toLowerCase()))
    }))
    .filter((group) => group.options.length > 0);

  function toggle(option: string) {
    if (selected.includes(option)) {
      onChange(selected.filter((item) => item !== option));
    } else {
      onChange([...selected, option]);
    }
  }

  function addCustom() {
    const value = query.trim();
    if (value && !selected.includes(value)) {
      onChange([...selected, value]);
      setQuery("");
    }
  }

  return (
    <div>
      <label>
        <span className="text-sm font-medium">{label}</span>
        <input
          className="mt-2 h-10 w-full rounded-md border border-line px-3"
          placeholder={placeholder}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <ChipList values={selected} onRemove={(value) => onChange(selected.filter((item) => item !== value))} />
      {allowCustom && query.trim() && (
        <button className="mt-3 rounded-md border border-line px-3 py-2 text-sm" type="button" onClick={addCustom}>
          Add custom role: {query.trim()}
        </button>
      )}
      <div className="mt-3 grid gap-3">
        {visibleGroups.map((group) => (
          <div key={group.label}>
            <h4 className="text-xs font-semibold uppercase tracking-[0.14em] text-[#6b756d]">{group.label}</h4>
            <div className="mt-2 flex flex-wrap gap-2">
              {group.options.map((option) => (
                <button
                  key={option}
                  className={`rounded-full border px-3 py-1 text-sm ${
                    selected.includes(option) ? "border-pine bg-pine text-white" : "border-line bg-white"
                  }`}
                  type="button"
                  onClick={() => toggle(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ChipInput({
  label,
  values,
  quickOptions,
  placeholder,
  onChange
}: {
  label: string;
  values: string[];
  quickOptions: string[];
  placeholder: string;
  onChange: (value: string[]) => void;
}) {
  const [input, setInput] = useState("");

  function add(raw: string) {
    const next = mergeLists(values, split(raw));
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
            placeholder={placeholder}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add(input);
              }
            }}
          />
          <Button variant="secondary" type="button" onClick={() => add(input)}>
            <Plus className="h-4 w-4" /> Add
          </Button>
        </div>
      </label>
      <ChipList values={values} onRemove={(value) => onChange(values.filter((item) => item !== value))} />
      {quickOptions.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {quickOptions.slice(0, 16).map((option) => (
            <button
              key={option}
              className="rounded-full border border-line px-3 py-1 text-sm"
              type="button"
              onClick={() => add(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ChipList({ values, onRemove }: { values: string[]; onRemove: (value: string) => void }) {
  if (values.length === 0) {
    return null;
  }
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {values.map((value) => (
        <span key={value} className="inline-flex items-center gap-2 rounded-full border border-line bg-panel px-3 py-1 text-sm">
          {value}
          <button type="button" onClick={() => onRemove(value)} aria-label={`Remove ${value}`}>
            <X className="h-3.5 w-3.5" />
          </button>
        </span>
      ))}
    </div>
  );
}

function EducationEditor({ records, onChange }: { records: EducationRecord[]; onChange: (records: EducationRecord[]) => void }) {
  return (
    <RepeatableSection
      title="education"
      emptyLabel="Add your first education entry"
      records={records}
      newRecord={() => ({ school: "", degree: "", major: "", minor: "", start_date: "", end_date: "", gpa: "", honors: [], coursework: [] })}
      onChange={onChange}
      render={(record, index, setRecord) => (
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="School" value={record.school} onChange={(value) => setRecord({ ...record, school: value })} />
          <Field label="Degree" value={record.degree} onChange={(value) => setRecord({ ...record, degree: value })} />
          <Field label="Major" value={record.major} onChange={(value) => setRecord({ ...record, major: value })} />
          <Field label="Minor" value={record.minor} onChange={(value) => setRecord({ ...record, minor: value })} />
          <Field label="Start date" type="date" value={record.start_date} onChange={(value) => setRecord({ ...record, start_date: value })} />
          <Field label="End date" type="date" value={record.end_date} onChange={(value) => setRecord({ ...record, end_date: value })} />
          <Field label="GPA" value={record.gpa} onChange={(value) => setRecord({ ...record, gpa: value })} />
          <ChipInput label="Honors" values={record.honors} quickOptions={[]} placeholder="Add honors" onChange={(value) => setRecord({ ...record, honors: value })} />
          <div className="md:col-span-2">
            <ChipInput label="Relevant coursework" values={record.coursework} quickOptions={[]} placeholder="Add coursework" onChange={(value) => setRecord({ ...record, coursework: value })} />
          </div>
          <input type="hidden" value={index} readOnly />
        </div>
      )}
    />
  );
}

function ExperienceEditor({ records, onChange }: { records: ExperienceRecord[]; onChange: (records: ExperienceRecord[]) => void }) {
  return (
    <RepeatableSection
      title="experience"
      emptyLabel="Add your first experience"
      records={records}
      newRecord={() => ({ company: "", title: "", location: "", start_date: "", end_date: "", currently_working: false, bullets: [], technologies: [], measurable_impact: [] })}
      onChange={onChange}
      render={(record, index, setRecord) => (
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Company" value={record.company} onChange={(value) => setRecord({ ...record, company: value })} />
          <Field label="Title" value={record.title} onChange={(value) => setRecord({ ...record, title: value })} />
          <Field label="Location" value={record.location} onChange={(value) => setRecord({ ...record, location: value })} />
          <Field label="Start date" type="date" value={record.start_date} onChange={(value) => setRecord({ ...record, start_date: value })} />
          <Field label="End date" type="date" value={record.end_date} onChange={(value) => setRecord({ ...record, end_date: value })} />
          <Toggle label="Currently working here" checked={record.currently_working} onChange={(value) => setRecord({ ...record, currently_working: value })} />
          <div className="md:col-span-2">
            <TextList label="Description / bullets" values={record.bullets} onChange={(value) => setRecord({ ...record, bullets: value })} />
          </div>
          <ChipInput label="Technologies" values={record.technologies} quickOptions={[]} placeholder="Add technologies" onChange={(value) => setRecord({ ...record, technologies: value })} />
          <ChipInput label="Measurable impact" values={record.measurable_impact} quickOptions={[]} placeholder="Add impact statements" onChange={(value) => setRecord({ ...record, measurable_impact: value })} />
          <input type="hidden" value={index} readOnly />
        </div>
      )}
    />
  );
}

function ProjectEditor({ records, onChange }: { records: ProjectRecord[]; onChange: (records: ProjectRecord[]) => void }) {
  return (
    <RepeatableSection
      title="project"
      emptyLabel="Add your first project"
      records={records}
      newRecord={() => ({ name: "", description: "", bullets: [], technologies: [], links: [], start_date: "", end_date: "" })}
      onChange={onChange}
      render={(record, index, setRecord) => (
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Project name" value={record.name} onChange={(value) => setRecord({ ...record, name: value })} />
          <Field label="Start date" type="date" value={record.start_date} onChange={(value) => setRecord({ ...record, start_date: value })} />
          <Field label="End date" type="date" value={record.end_date} onChange={(value) => setRecord({ ...record, end_date: value })} />
          <div className="md:col-span-2">
            <TextArea label="Description" value={record.description} onChange={(value) => setRecord({ ...record, description: value })} />
          </div>
          <div className="md:col-span-2">
            <TextList label="Bullets" values={record.bullets} onChange={(value) => setRecord({ ...record, bullets: value })} />
          </div>
          <ChipInput label="Technologies" values={record.technologies} quickOptions={[]} placeholder="Add technologies" onChange={(value) => setRecord({ ...record, technologies: value })} />
          <ChipInput label="Links" values={record.links} quickOptions={[]} placeholder="Add project links" onChange={(value) => setRecord({ ...record, links: value })} />
          <input type="hidden" value={index} readOnly />
        </div>
      )}
    />
  );
}

function RepeatableSection<T>({
  title,
  emptyLabel,
  records,
  newRecord,
  onChange,
  render
}: {
  title: string;
  emptyLabel: string;
  records: T[];
  newRecord: () => T;
  onChange: (records: T[]) => void;
  render: (record: T, index: number, setRecord: (record: T) => void) => ReactNode;
}) {
  function setRecord(index: number, record: T) {
    onChange(records.map((item, itemIndex) => (itemIndex === index ? record : item)));
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-[#5d675f]">Add, edit, or delete {title} records. Save persists all changes.</p>
        <Button type="button" onClick={() => onChange([...records, newRecord()])}>
          <Plus className="h-4 w-4" /> Add {title}
        </Button>
      </div>
      {records.length === 0 && (
        <button
          className="rounded-lg border border-dashed border-line bg-panel p-8 text-center text-sm font-medium text-[#5d675f]"
          type="button"
          onClick={() => onChange([newRecord()])}
        >
          {emptyLabel}
        </button>
      )}
      {records.map((record, index) => (
        <div key={index} className="rounded-lg border border-line p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h3 className="font-semibold">{title[0].toUpperCase() + title.slice(1)} {index + 1}</h3>
            <Button
              variant="danger"
              type="button"
              onClick={() => onChange(records.filter((_, itemIndex) => itemIndex !== index))}
            >
              <Trash2 className="h-4 w-4" /> Delete
            </Button>
          </div>
          {render(record, index, (next) => setRecord(index, next))}
        </div>
      ))}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  required = false
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  required?: boolean;
}) {
  return (
    <label>
      <span className="text-sm font-medium">{label}{required ? " *" : ""}</span>
      <input
        className="mt-2 h-10 w-full rounded-md border border-line px-3"
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function TextArea({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label>
      <span className="text-sm font-medium">{label}</span>
      <textarea className="mt-2 min-h-28 w-full rounded-md border border-line p-3" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function TextList({ label, values, onChange }: { label: string; values: string[]; onChange: (value: string[]) => void }) {
  return (
    <TextArea
      label={label}
      value={values.join("\n")}
      onChange={(value) => onChange(value.split("\n").map((item) => item.trim()).filter(Boolean))}
    />
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

function ReviewBlock({ title, data }: { title: string; data: unknown }) {
  return (
    <section>
      <h3 className="mb-2 font-semibold">{title}</h3>
      <pre className="overflow-auto rounded-md bg-[#17211b] p-4 text-xs text-white">
        {JSON.stringify(data, null, 2)}
      </pre>
    </section>
  );
}

function formFromProfile(
  profile: Partial<ProfileForm> & {
    work_authorization_status?: string | null;
    work_authorization?: string | null;
    work_preference?: ProfileForm["remote_preference"] | null;
    remote_preference?: ProfileForm["remote_preference"] | null;
  }
): ProfileForm {
  return {
    ...emptyProfile,
    ...profile,
    full_name: profile.full_name ?? "",
    phone: profile.phone ?? "",
    location_city: profile.location_city ?? "",
    location_state: profile.location_state ?? "",
    location_country: profile.location_country ?? "United States",
    work_authorization: profile.work_authorization_status ?? profile.work_authorization ?? "prefer_not_to_say",
    requires_sponsorship: Boolean(profile.requires_sponsorship),
    open_to_relocation: Boolean(profile.open_to_relocation),
    target_roles: profile.target_roles ?? [],
    target_levels: profile.target_levels ?? [],
    preferred_locations: profile.preferred_locations ?? [],
    remote_preference: profile.work_preference ?? profile.remote_preference ?? "everything",
    skills: profile.skills ?? [],
    linkedin_url: profile.linkedin_url ?? "",
    github_url: profile.github_url ?? "",
    portfolio_url: profile.portfolio_url ?? ""
  };
}

function careerFromResponse(careerResult: Partial<CareerForm>): CareerForm {
  return {
    education: normalizeEducationList(careerResult.education ?? []),
    experience: normalizeExperienceList(careerResult.experience ?? []),
    projects: normalizeProjectList(careerResult.projects ?? []),
    certifications: careerResult.certifications ?? [],
    awards: careerResult.awards ?? []
  };
}

function split(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function unique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)));
}

function mergeLists(current: string[], incoming: string[]) {
  return unique([...current, ...incoming.map(String).map((item) => item.trim())]);
}

function isValidOptionalUrl(value: string) {
  return !value || value.startsWith("http://") || value.startsWith("https://");
}

function cleanDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

function normalizeDate(value: unknown) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "";
}

function normalizeEducationList(records: unknown[]): EducationRecord[] {
  return records.map((record) => {
    const item = record as Partial<EducationRecord>;
    return {
      school: item.school ?? "",
      degree: item.degree ?? "",
      major: item.major ?? "",
      minor: item.minor ?? "",
      start_date: normalizeDate(item.start_date),
      end_date: normalizeDate(item.end_date),
      gpa: item.gpa ?? "",
      honors: item.honors ?? [],
      coursework: item.coursework ?? []
    };
  });
}

function normalizeExperienceList(records: unknown[]): ExperienceRecord[] {
  return records.map((record) => {
    const item = record as Partial<ExperienceRecord>;
    return {
      company: item.company ?? "",
      title: item.title ?? "",
      location: item.location ?? "",
      start_date: normalizeDate(item.start_date),
      end_date: normalizeDate(item.end_date),
      currently_working: Boolean(item.currently_working),
      bullets: item.bullets ?? [],
      technologies: item.technologies ?? [],
      measurable_impact: item.measurable_impact ?? []
    };
  });
}

function normalizeProjectList(records: unknown[]): ProjectRecord[] {
  return records.map((record) => {
    const item = record as Partial<ProjectRecord>;
    return {
      name: item.name ?? "",
      description: item.description ?? "",
      bullets: item.bullets ?? [],
      technologies: item.technologies ?? [],
      links: item.links ?? [],
      start_date: normalizeDate(item.start_date),
      end_date: normalizeDate(item.end_date)
    };
  });
}

function cleanEducation(record: EducationRecord) {
  return {
    ...record,
    school: record.school || "Untitled school",
    start_date: cleanDate(record.start_date),
    end_date: cleanDate(record.end_date)
  };
}

function cleanExperience(record: ExperienceRecord) {
  return {
    ...record,
    company: record.company || "Untitled company",
    title: record.title || "Untitled role",
    start_date: cleanDate(record.start_date),
    end_date: cleanDate(record.end_date)
  };
}

function cleanProject(record: ProjectRecord) {
  return {
    ...record,
    name: record.name || "Untitled project",
    start_date: cleanDate(record.start_date),
    end_date: cleanDate(record.end_date)
  };
}
