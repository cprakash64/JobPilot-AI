"use client";

/**
 * Voluntary EEO form.
 *
 * Replaces a version that reused ONE Yes/No/Another option list across five
 * unrelated questions — so "Gender" offered Yes/No, and race collapsed into a
 * meaningless "Another option". Each question now has its own vocabulary
 * (lib/eeo.ts, mirroring the server's app/profile/eeo.py).
 *
 * Behaviour that is deliberate, not incidental:
 *   - nothing is preselected; "not answered" and "prefer not to answer" are
 *     different states, and only the user can choose the latter;
 *   - consent is unchecked by default, and pressing Save is NOT consent;
 *   - saving answers without consent is refused by the server (422);
 *   - clearing consent deletes stored values, after an explicit confirmation;
 *   - race/ethnicity is multi-select and "Prefer not to answer" is exclusive.
 */

import { useEffect, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/Button";
import { SectionError, toSectionError, type SectionErrorInfo } from "@/components/SectionError";
import { api } from "@/lib/api";
import {
  DISABILITY_STATUS_OPTIONS,
  GENDER_IDENTITY_OPTIONS,
  HISPANIC_OR_LATINO_OPTIONS,
  RACE_ETHNICITY_OPTIONS,
  VETERAN_STATUS_OPTIONS,
  emptyEeoForm,
  hasAnyAnswer,
  normalizeEeo,
  toggleRaceSelection,
  type EeoForm,
  type EeoOption
} from "@/lib/eeo";

export function DemographicsForm() {
  const [form, setForm] = useState<EeoForm>(emptyEeoForm);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [needsReview, setNeedsReview] = useState(false);
  const [error, setError] = useState<SectionErrorInfo | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await api<{
          demographics: (Partial<EeoForm> & { needs_review?: boolean }) | null;
        }>("/profile/demographics");
        if (!mounted) return;
        setForm(normalizeEeo(result.demographics));
        setNeedsReview(Boolean(result.demographics?.needs_review));
      } catch (loadError) {
        if (mounted) setError(toSectionError(loadError));
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [reloadToken]);

  function set<K extends keyof EeoForm>(key: K, value: EeoForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setMessage("");
  }

  async function save() {
    setMessage("");
    setError(null);

    // Mirrors the server rule so the common case gets immediate feedback rather
    // than a round trip. The server stays the authority.
    if (hasAnyAnswer(form) && !form.consent_to_store) {
      setError({
        message:
          "Tick the consent box to store these answers, or clear them and save to remove stored data.",
        retryable: false
      });
      return;
    }

    // Clearing consent deletes stored values — always confirmed explicitly.
    if (!form.consent_to_store) {
      const confirmed =
        typeof window === "undefined" ||
        window.confirm(
          "This will delete any demographic information JobPilot has stored for you. Continue?"
        );
      if (!confirmed) return;
    }

    setSaving(true);
    try {
      await api("/profile/demographics", { method: "PUT", body: JSON.stringify(form) });
      setMessage(
        form.consent_to_store
          ? "Saved. This information is stored separately and is never used for job matching."
          : "Stored demographic information deleted."
      );
      setNeedsReview(false);
      if (!form.consent_to_store) setForm(emptyEeoForm);
    } catch (saveError) {
      setError(toSectionError(saveError));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete all stored demographic information? This cannot be undone.")
    ) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api("/profile/demographics", { method: "DELETE" });
      setForm(emptyEeoForm);
      setNeedsReview(false);
      setMessage("Demographic information deleted.");
    } catch (deleteError) {
      setError(toSectionError(deleteError));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <section className="rounded-lg border border-line bg-white p-5">
        <p className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading your settings…
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-line bg-white p-5">
      <div className="rounded-md border border-line bg-panel p-4 text-sm leading-6 text-[var(--text-secondary)]">
        This information is optional and entirely up to you. &ldquo;Prefer not to answer&rdquo; is always
        available. It is stored separately from your career profile, is <strong>never</strong> used for
        job-fit scoring, ranking, resumes or cover letters, and you can delete it at any time.
      </div>

      {needsReview && (
        <p
          role="status"
          className="mt-4 rounded-md border border-[var(--warning-border)] bg-[var(--warning-surface)] px-3 py-2 text-sm text-[var(--warning)]"
        >
          Some previously stored answers used options that are no longer valid, so they were cleared.
          Please re-answer any questions you want on file.
        </p>
      )}

      {error && (
        <div className="mt-4">
          <SectionError
            error={error}
            title="This could not be saved"
            onRetry={error.retryable ? () => setReloadToken((token) => token + 1) : undefined}
          />
        </div>
      )}

      <div className="mt-5 grid gap-6">
        <ChoiceQuestion
          legend="Gender identity"
          name="gender_identity"
          options={GENDER_IDENTITY_OPTIONS}
          value={form.gender_identity}
          onChange={(value) => set("gender_identity", value)}
        />
        {form.gender_identity === "self_describe" && (
          <SelfDescribe
            label="How would you describe your gender identity? (optional)"
            value={form.gender_self_description}
            onChange={(value) => set("gender_self_description", value)}
          />
        )}

        <ChoiceQuestion
          legend="Veteran status"
          name="veteran_status"
          options={VETERAN_STATUS_OPTIONS}
          value={form.veteran_status}
          onChange={(value) => set("veteran_status", value)}
        />

        <ChoiceQuestion
          legend="Disability status"
          name="disability_status"
          options={DISABILITY_STATUS_OPTIONS}
          value={form.disability_status}
          onChange={(value) => set("disability_status", value)}
        />

        <ChoiceQuestion
          legend="Are you Hispanic or Latino?"
          name="hispanic_or_latino"
          options={HISPANIC_OR_LATINO_OPTIONS}
          value={form.hispanic_or_latino}
          onChange={(value) => set("hispanic_or_latino", value)}
        />

        {/* Multi-select: people identify with more than one category, and
            collapsing that to one value loses real information. */}
        <fieldset className="border-0 p-0">
          <legend className="text-sm font-medium text-[var(--text-primary)]">Race and ethnicity</legend>
          <p className="mt-1 text-xs text-[var(--text-muted)]">Select all that apply.</p>
          <div className="mt-3 grid gap-2">
            {RACE_ETHNICITY_OPTIONS.map((option) => (
              <label
                key={option.value}
                className="flex items-start gap-3 text-sm text-[var(--text-primary)]"
              >
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={form.race_ethnicity.includes(option.value)}
                  onChange={() =>
                    set("race_ethnicity", toggleRaceSelection(form.race_ethnicity, option.value))
                  }
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        {form.race_ethnicity.includes("another_race_or_ethnicity") && (
          <SelfDescribe
            label="How would you describe your race or ethnicity? (optional)"
            value={form.race_self_description}
            onChange={(value) => set("race_self_description", value)}
          />
        )}

        {/* Unchecked by default. Saving is not consent. */}
        <label className="flex items-start gap-3 rounded-md border border-line bg-[var(--surface-muted)] p-4">
          <input
            type="checkbox"
            className="mt-1"
            checked={form.consent_to_store}
            onChange={(event) => set("consent_to_store", event.target.checked)}
          />
          <span className="text-sm text-[var(--text-primary)]">
            I consent to JobPilot AI securely storing this optional demographic information for assisted
            application filling.
          </span>
        </label>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          Save settings
        </Button>
        <Button variant="danger" type="button" onClick={remove} disabled={saving}>
          <Trash2 className="h-4 w-4" aria-hidden="true" /> Delete EEO data
        </Button>
        {message && (
          <p role="status" className="text-sm text-[var(--success)]">
            {message}
          </p>
        )}
      </div>
    </section>
  );
}

/**
 * A single-answer question rendered as radios, so "nothing selected" is a
 * representable state. A <select> cannot express that without a placeholder
 * option, which reads like an answer and is exactly how a default value sneaks
 * into voluntary data.
 */
function ChoiceQuestion({
  legend,
  name,
  options,
  value,
  onChange
}: {
  legend: string;
  name: string;
  options: EeoOption[];
  value: string | null;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="border-0 p-0">
      <legend className="text-sm font-medium text-[var(--text-primary)]">{legend}</legend>
      <div className="mt-3 grid gap-2">
        {options.map((option) => (
          <label
            key={option.value}
            className="flex items-start gap-3 text-sm text-[var(--text-primary)]"
          >
            <input
              type="radio"
              className="mt-0.5"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function SelfDescribe({
  label,
  value,
  onChange
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-[var(--text-primary)]">{label}</span>
      <input
        className="mt-2 h-10 w-full rounded-md border border-[var(--border)] bg-[var(--input-background)] px-3 text-[var(--text-primary)]"
        value={value}
        maxLength={200}
        onChange={(event) => onChange(event.target.value)}
      />
      <span className="mt-1 block text-xs text-[var(--text-muted)]">
        Stored only if you consent below, and never used for matching.
      </span>
    </label>
  );
}
