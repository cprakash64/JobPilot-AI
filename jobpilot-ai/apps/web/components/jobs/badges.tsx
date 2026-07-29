import { Banknote, Loader2, MapPin } from "lucide-react";
import type { ScoreState } from "@/lib/api";
import { getFitScoreTone, getScoreDisplay } from "@/lib/fitScore";
import { postedLabel, sourceLabel } from "@/components/jobs/format";

export function Meta({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[var(--text-muted)]">{icon}</span>
      {children}
    </span>
  );
}

export function SourceBadge({ source }: { source: string | null }) {
  const label = sourceLabel(source);
  if (!label) {
    return null;
  }
  return <span className="rounded-full border border-line px-2 py-0.5 text-xs text-[var(--text-muted)]">{label}</span>;
}

export function WorkplaceBadge({ type }: { type: string | null }) {
  if (!type || type === "unknown") {
    return null;
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs">
      <MapPin className="h-3 w-3" /> {type[0].toUpperCase() + type.slice(1)}
    </span>
  );
}

export function PostedBadge({ postedAt }: { postedAt: string | null }) {
  const label = postedLabel(postedAt);
  if (!label) {
    return null;
  }
  return <span className="rounded-full bg-panel px-2 py-0.5 text-xs text-[var(--text-muted)]">{label}</span>;
}

/**
 * Compensation, carried at the same weight on the card and in the detail
 * header. Callers render it only when the employer actually published a range —
 * there is deliberately no "not listed" state to render.
 */
export function SalaryChip({ value, size = "md" }: { value: string; size?: "md" | "lg" }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-[var(--surface-raised)] font-semibold text-[var(--text-primary)] ${
        size === "lg" ? "h-11 px-3.5 text-[15px]" : "h-7 px-2.5 text-[13px]"
      }`}
    >
      <Banknote
        className={`${size === "lg" ? "h-4 w-4" : "h-3.5 w-3.5"} text-[var(--text-muted)]`}
        aria-hidden
      />
      <span className="sr-only">Salary range </span>
      {value}
    </span>
  );
}

/**
 * The score card used on full job cards and in the detail header.
 *
 * Meaning never rests on colour alone: the number and the band label are always
 * rendered, and non-scored lifecycle states say what is happening instead of
 * showing a permanent "Not scored".
 */
export function FitBadge({
  score,
  label,
  scoreState
}: {
  score: number | null;
  label: string | null;
  scoreState?: ScoreState | null;
}) {
  const display = getScoreDisplay(scoreState, score);

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

/**
 * Single-line fit indicator for the narrow list column. Same tone system as
 * FitBadge, with a fixed width so a scored and an unscored row never shift the
 * rest of the card.
 */
export function FitPill({
  score,
  label,
  scoreState
}: {
  score: number | null;
  label: string | null;
  scoreState?: ScoreState | null;
}) {
  const display = getScoreDisplay(scoreState, score);
  if (display.kind !== "score") {
    return (
      <span
        data-fit-tone="none"
        data-score-state={scoreState ?? "pending"}
        className="inline-flex h-6 shrink-0 items-center gap-1 rounded-full border border-line bg-panel px-2 text-[11px] font-medium text-[var(--text-muted)]"
      >
        {display.kind === "calculating" ? (
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        ) : null}
        {display.kind === "calculating" ? "Scoring" : "No score"}
      </span>
    );
  }
  const tone = getFitScoreTone(score);
  const rounded = score !== null ? Math.round(score) : null;
  return (
    <span
      data-fit-tone={tone.key}
      data-score-state="scored"
      className={`inline-flex h-6 shrink-0 items-center rounded-full border px-2 text-[11px] font-semibold ${tone.container}`}
      title={tone.description}
    >
      <span aria-hidden>{rounded}</span>
      <span className="sr-only">{`Fit score ${rounded} out of 100, ${label ?? tone.label}`}</span>
    </span>
  );
}
