"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
import { DemographicsForm } from "@/components/DemographicsForm";
import {
  ImportProfilePreview,
  type EditableImportDraft,
  type ImportApplyMode,
  type ImportSection
} from "@/components/ImportProfilePreview";
import { api, ApiError } from "@/lib/api";
import { validateApplicationEmail } from "@/lib/emailValidation";
import { composeFullName } from "@/lib/names";
import { SectionError, toSectionError, type SectionErrorInfo } from "@/components/SectionError";
import {
  emptyProfile,
  normalizeProfile,
  profileToWire,
  type ProfileForm,
  type ProfileWire
} from "@/lib/profileForm";

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


const emptyCareer: CareerForm = {
  education: [],
  experience: [],
  projects: [],
  certifications: [],
  awards: []
};


/** Turn a FastAPI 422 validation body into per-field messages.
 *
 * FastAPI reports `detail: [{loc: ["body", "field"], msg}]`. Anything that is
 * not that shape yields {} so the caller falls back to a banner. Never returns
 * the submitted VALUE, only the field name and the reason. */
function fieldErrorsFromApi(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.status !== 422) return {};
  // For an HTTP error the api() helper leaves the raw body in `message` when it
  // is not a plain `detail` string — which is exactly the FastAPI 422 shape.
  const raw = error.details ?? error.message;
  let detail: unknown = raw;
  if (typeof raw === "string") {
    try { detail = JSON.parse(raw); } catch { return {}; }
  }
  const items = Array.isArray(detail)
    ? detail
    : Array.isArray((detail as { detail?: unknown })?.detail)
      ? (detail as { detail: unknown[] }).detail
      : [];
  const result: Record<string, string> = {};
  for (const item of items) {
    const loc = (item as { loc?: unknown[] })?.loc;
    const msg = (item as { msg?: unknown })?.msg;
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const field = loc.filter((p) => typeof p === "string" && p !== "body").pop();
    if (typeof field === "string") result[field] = msg;
  }
  return result;
}

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
  const [loadError, setLoadError] = useState<SectionErrorInfo | null>(null);
  // Bumping this re-runs the load effect — the Retry affordance.
  const [reloadToken, setReloadToken] = useState(0);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  // True once the user has edited anything. The profile GET must never
  // overwrite their in-progress edits if it resolves late — that silently
  // discarded typed input and then failed validation on save.
  const [dirty, setDirty] = useState(false);
  const [form, setForm] = useState<ProfileForm>(emptyProfile);
  const [career, setCareer] = useState<CareerForm>(emptyCareer);

  // Current employer is not a profile column — it is the most recent
  // Experience entry, shown read-only so the two cannot disagree.
  const currentEmployer = useMemo(() => {
    const current = career.experience.find((item) => item.currently_working) ?? career.experience[0];
    return current?.company ?? "";
  }, [career.experience]);

  const derivedFullName = useMemo(
    () =>
      composeFullName({
        firstName: form.first_name,
        middleName: form.middle_name,
        lastName: form.last_name
      }),
    [form.first_name, form.middle_name, form.last_name]
  );

  const suggestedSkills = useMemo(() => {
    const suggestions = form.target_roles.flatMap((role) => skillSuggestions[role] ?? []);
    return unique(suggestions).filter((skill) => !form.skills.includes(skill));
  }, [form.target_roles, form.skills]);

  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  useEffect(() => {
    let mounted = true;
    async function loadProfile() {
      setLoading(true);
      setLoadError(null);
      try {
        const [profileResult, careerResult, account] = await Promise.all([
          api<{ profile: ProfileWire | null }>("/profile"),
          api<Partial<CareerForm>>("/profile/career"),
          // Email lives on the account, not the profile record.
          api<{ email?: string }>("/auth/me").catch(() => ({ email: "" }))
        ]);
        if (!mounted) {
          return;
        }
        // ONE normalization boundary — see lib/profileForm.ts. Never spread an
        // API profile straight into form state: the wire sends null for any
        // unset column, and a null in a controlled input crashes the page.
        // Only seed the form while it is still pristine.
        setForm((current) =>
          dirtyRef.current ? current : normalizeProfile(profileResult.profile, account?.email ?? "")
        );
        setCareer({
          education: normalizeEducationList(careerResult.education ?? []),
          experience: normalizeExperienceList(careerResult.experience ?? []),
          projects: normalizeProjectList(careerResult.projects ?? []),
          certifications: careerResult.certifications ?? [],
          awards: careerResult.awards ?? []
        });
      } catch (loadError) {
        if (mounted) {
          // A failed load is a SECTION-level problem, not a page crash: the
          // wizard still renders, with a retry affordance and the request id.
          setLoadError(toSectionError(loadError));
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
  }, [reloadToken]);

  function update<K extends keyof ProfileForm>(key: K, value: ProfileForm[K]) {
    setDirty(true);
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
    if (!form.first_name.trim() || !form.last_name.trim()) {
      setError("First and last name are required.");
      setStep(1);
      return;
    }
    if (![form.linkedin_url, form.github_url, form.portfolio_url].every(isValidOptionalUrl)) {
      setError("Links must start with http:// or https://.");
      setStep(7);
      return;
    }
    setSaving(true);
    setFieldErrors({});
    try {
      // profileToWire() is the counterpart of normalizeProfile(): one place
      // that knows the wire shape, so form state and payload cannot drift.
      await api("/profile", { method: "PUT", body: JSON.stringify(profileToWire(form)) });
      // The structured name goes through its own endpoint: PUT /profile is a
      // full overwrite that deliberately does not carry the name parts or the
      // confirmation flag. Editing the name here IS an explicit confirmation of
      // the split, so it is never re-derived from full_name afterwards. Runs
      // after PUT /profile so a first-time save has a profile to attach to.
      await api("/profile/name", {
        method: "PUT",
        body: JSON.stringify({
          first_name: form.first_name.trim(),
          middle_name: form.middle_name.trim() || null,
          last_name: form.last_name.trim(),
          preferred_first_name: form.preferred_first_name.trim() || null,
          preferred_last_name: form.preferred_last_name.trim() || null
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
      // Field-level validation (422) is shown against the offending fields
      // rather than as an opaque banner — and never crashes the page.
      const perField = fieldErrorsFromApi(saveError);
      if (Object.keys(perField).length > 0) {
        setFieldErrors(perField);
        setError("Some fields need attention before this can be saved.");
        setStep(1);
      } else {
        setError(saveError instanceof Error ? saveError.message : "Could not save profile.");
      }
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
        setForm(normalizeProfile(result.profile as ProfileWire, form.email));
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
                index === step ? "bg-panel font-medium text-pine" : "text-[var(--text-muted)]"
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
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              {loading ? "Loading saved profile..." : "Update any section and save when ready."}
            </p>
          </div>
          <Button type="button" onClick={save} disabled={saving || loading}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save profile
          </Button>
        </div>

        {message && (
          <p className="mt-4 rounded-md border border-[var(--success-border)] bg-[var(--success-surface)] px-3 py-2 text-sm text-pine">{message}</p>
        )}
        {error && (
          <p className="mt-4 rounded-md border border-[var(--danger-border)] bg-[var(--danger-surface)] px-3 py-2 text-sm text-[var(--danger)]">{error}</p>
        )}

        <div className="mt-5">
          {/* A failed load is section-scoped: the wizard chrome stays usable and
              the user gets Retry / Back to Import instead of losing the page to
              the global error boundary. */}
          {loadError && (
            <SectionError
              error={loadError}
              onRetry={() => { setDirty(false); dirtyRef.current = false; setReloadToken((token) => token + 1); }}
              onBack={() => { setLoadError(null); setStep(0); }}
            />
          )}

          {!loadError && step === 0 && (
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

          {!loadError && step === 1 && (
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="First name" value={form.first_name} required onChange={(value) => update("first_name", value)} error={fieldErrors.first_name} />
              <Field label="Middle name (optional)" value={form.middle_name} onChange={(value) => update("middle_name", value)} />
              <Field label="Last name" value={form.last_name} required onChange={(value) => update("last_name", value)} error={fieldErrors.last_name} />
              <div className="md:col-span-2 -mt-2 text-xs text-neutral-500">
                Applications will use{" "}
<strong>{derivedFullName || "your full name"}</strong>{" "}for a
                &ldquo;full name&rdquo; field, and only <strong>{form.last_name || "your last name"}</strong>{" "}
                for a &ldquo;last name&rdquo; field.
              </div>
              <Field
                label="Preferred first name (optional)"
                value={form.preferred_first_name}
                onChange={(value) => update("preferred_first_name", value)}
              />
              <Field
                label="Preferred last name (optional)"
                value={form.preferred_last_name}
                onChange={(value) => update("preferred_last_name", value)}
              />
              <Field
                label="Email"
                value={form.email}
                readOnly
                hint="Your JobPilot login. Change it in Settings."
              />
              <Field
                label="Application email"
                type="email"
                value={form.application_email}
                onChange={(value) => update("application_email", value)}
                error={fieldErrors.application_email ?? validateApplicationEmail(form.application_email) ?? undefined}
                hint="This email will be used on job applications. It can be different from your JobPilot login email."
              />
              <label>
                <span className="text-sm font-medium">Phone country</span>
                <select
                  className="mt-2 h-10 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] px-3 text-[var(--text-primary)]"
                  value={form.phone_country_iso2}
                  onChange={(event) => update("phone_country_iso2", event.target.value)}
                >
                  {phoneCountryOptions.map(([iso2, label]) => (
                    <option key={iso2} value={iso2}>{label}</option>
                  ))}
                </select>
              </label>
              <Field
                label="Phone number"
                value={form.phone}
                onChange={(value) => update("phone", value)}
                error={fieldErrors.phone}
                hint="Saved in international format so application forms accept it."
              />
              <Field label="City" value={form.location_city} onChange={(value) => update("location_city", value)} error={fieldErrors.location_city} />
              <Field label="State/region" value={form.location_state} onChange={(value) => update("location_state", value)} />
              <Field label="ZIP/postal code" value={form.location_postal_code} onChange={(value) => update("location_postal_code", value)} />
              <Field label="Country" value={form.location_country} onChange={(value) => update("location_country", value)} />
              <Field
                label="Current employer"
                value={currentEmployer}
                readOnly
                hint="Taken from your most recent Experience entry."
              />
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
              <label>
                <span className="text-sm font-medium">
                  Will you now or in the future require company sponsorship to retain or extend your work authorization?
                </span>
                <select
                  className="mt-2 h-10 w-full rounded-md border border-line bg-white px-3"
                  value={form.requires_sponsorship ? "yes" : "no"}
                  onChange={(event) => update("requires_sponsorship", event.target.value === "yes")}
                >
                  <option value="no">No</option>
                  <option value="yes">Yes</option>
                </select>
                <span className="mt-1 block text-xs text-muted">
                  JobPilot uses this answer for sponsorship questions on employer applications.
                </span>
              </label>
              <Toggle
                label="Open to relocation"
                checked={form.open_to_relocation}
                onChange={(value) => update("open_to_relocation", value)}
              />
            </div>
          )}

          {!loadError && step === 2 && (
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

          {!loadError && step === 3 && (
            <EducationEditor
              records={career.education}
              onChange={(education) => setCareer((current) => ({ ...current, education }))}
            />
          )}

          {!loadError && step === 4 && (
            <ExperienceEditor
              records={career.experience}
              onChange={(experience) => setCareer((current) => ({ ...current, experience }))}
            />
          )}

          {!loadError && step === 5 && (
            <ProjectEditor
              records={career.projects}
              onChange={(projects) => setCareer((current) => ({ ...current, projects }))}
            />
          )}

          {!loadError && step === 6 && (
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

          {!loadError && step === 7 && (
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="LinkedIn" value={form.linkedin_url} onChange={(value) => update("linkedin_url", value)} />
              <Field label="GitHub" value={form.github_url} onChange={(value) => update("github_url", value)} />
              <Field label="Portfolio" value={form.portfolio_url} onChange={(value) => update("portfolio_url", value)} />
            </div>
          )}

          {!loadError && step === 8 && (
            <DemographicsForm />
          )}

          {!loadError && step === 9 && (
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
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--text-muted)]">
          Upload a resume, upload your LinkedIn PDF, or paste your profile text. JobPilot AI will extract a draft profile that you can review and edit.
        </p>
        <p className="mt-3 rounded-md border border-line bg-white px-3 py-2 text-sm font-medium">
          We do not log into LinkedIn or scrape your account. You control what you upload or paste.
        </p>
        <p className="mt-3 text-sm leading-6 text-[var(--text-muted)]">
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
          <p className="text-xs text-[var(--text-muted)]">
            We do not log into LinkedIn or scrape your account. You control what you upload or paste.
          </p>
          <button className="focus-ring rounded-md p-1.5" type="button" onClick={onClose} aria-label="Close import modal">
            <X className="h-5 w-5" />
          </button>
        </div>
        {applyError && (
          <p className="mx-5 mt-4 rounded-md border border-[var(--danger-border)] bg-[var(--danger-surface)] px-3 py-2 text-sm text-[var(--danger)]">
            {applyError}
          </p>
        )}
        {!draft ? (
          <div className="overflow-auto p-5">
            <h3 className="text-xl font-semibold">Paste profile text</h3>
            <p className="mb-3 mt-1 text-sm text-[var(--text-muted)]">
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
            <h4 className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">{group.label}</h4>
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
        <p className="text-sm text-[var(--text-muted)]">Add, edit, or delete {title} records. Save persists all changes.</p>
        <Button type="button" onClick={() => onChange([...records, newRecord()])}>
          <Plus className="h-4 w-4" /> Add {title}
        </Button>
      </div>
      {records.length === 0 && (
        <button
          className="rounded-lg border border-dashed border-line bg-panel p-8 text-center text-sm font-medium text-[var(--text-muted)]"
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
  required = false,
  readOnly = false,
  error,
  hint
}: {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  type?: string;
  required?: boolean;
  readOnly?: boolean;
  error?: string;
  hint?: string;
}) {
  // `value ?? ""` keeps the input CONTROLLED even if a caller ever hands it a
  // null: React warns (and the field silently stops accepting edits) when an
  // input flips between undefined and a string.
  const safeValue = value ?? "";
  return (
    <label className="block">
      <span className="text-sm font-medium text-[var(--text-primary)]">
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
        {required ? <span className="sr-only"> (required)</span> : null}
      </span>
      <input
        className={`mt-2 h-10 w-full rounded-md border px-3 bg-[var(--input-background)] text-[var(--text-primary)] ${
          error ? "border-[var(--danger)]" : "border-[var(--border)]"
        } ${readOnly ? "bg-[var(--disabled-background)] text-[var(--text-secondary)]" : ""}`}
        type={type}
        value={safeValue}
        readOnly={readOnly}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${label}-error` : hint ? `${label}-hint` : undefined}
        onChange={(event) => onChange?.(event.target.value)}
      />
      {/* Errors are announced and prefixed, never signalled by colour alone. */}
      {error && (
        <span id={`${label}-error`} role="alert" className="mt-1 block text-xs text-[var(--danger)]">
          Error: {error}
        </span>
      )}
      {!error && hint && (
        <span id={`${label}-hint`} className="mt-1 block text-xs text-[var(--text-muted)]">
          {hint}
        </span>
      )}
    </label>
  );
}

/** Phone countries offered in Basic info. Deliberately a short, common list —
 * the stored value is the ISO2 code, and the API normalizes the number to
 * E.164 using it (app/profile/phone.py). */
const phoneCountryOptions: [string, string][] = [
  ["US", "United States (+1)"],
  ["CA", "Canada (+1)"],
  ["GB", "United Kingdom (+44)"],
  ["IN", "India (+91)"],
  ["AU", "Australia (+61)"],
  ["DE", "Germany (+49)"],
  ["FR", "France (+33)"],
  ["SG", "Singapore (+65)"]
];

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
      <pre className="overflow-auto rounded-md bg-[var(--text-primary)] p-4 text-xs text-white">
        {JSON.stringify(data, null, 2)}
      </pre>
    </section>
  );
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
