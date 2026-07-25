"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, Download, ExternalLink, FileText, Loader2, Mail, RefreshCw, ShieldCheck, X } from "lucide-react";
import { Button } from "@/components/Button";
import {
  type CreatedApplicationSession,
  type ExtensionState,
  createApplicationSession,
  detectExtensionState,
  openOfficialSite,
  stageLaunch,
  startAssistedApply
} from "@/lib/autoApply";
import { ApiError, api } from "@/lib/api";

/** Where to get the extension. Update for the published store listing when live;
 * for local dev this points at the in-repo build + load instructions. */
const EXTENSION_INSTALL_URL =
  "https://github.com/jobpilot-ai/jobpilot-ai/tree/main/apps/extension#readme";

type Phase = "preparing" | "ready" | "opened" | "error";

type PreparationError = {
  message: string;
  retryable: boolean;
  /** When set, show a shortcut to the relevant Profile section. */
  profileLink?: boolean;
  requestId?: string;
};

type LaunchError = { code: string; message: string };

/** Turn a backend error into a specific, actionable message. The backend already
 * sends actionable copy; this maps a few known codes to guaranteed wording and
 * decides when to surface a Profile shortcut. Internal details are never shown. */
function toPreparationError(err: unknown): PreparationError {
  if (err instanceof ApiError) {
    switch (err.serverCode) {
      case "PROFILE_INCOMPLETE":
        return { message: err.message, retryable: false, profileLink: true, requestId: err.requestId };
      case "JOB_NOT_FOUND":
        return { message: "This job is no longer available in JobPilot.", retryable: false, requestId: err.requestId };
      case "INVALID_APPLICATION_URL":
        return { message: err.message, retryable: false, requestId: err.requestId };
      case "DATABASE_UNAVAILABLE":
        return {
          message: "The application service is temporarily unavailable. Please try again.",
          retryable: true,
          requestId: err.requestId
        };
    }
    if (err.code === "auth_expired") {
      return { message: "Your session expired. Sign in again.", retryable: false, requestId: err.requestId };
    }
    if (err.code === "rate_limited") {
      return { message: "Too many attempts. Wait a moment and try again.", retryable: true, requestId: err.requestId };
    }
    // Everything else (including network_unreachable) keeps the API client's own,
    // already-actionable message and retry hint.
    return { message: err.message, retryable: err.retryable, requestId: err.requestId };
  }
  return {
    message: err instanceof Error ? err.message : "Could not prepare your application. Please try again.",
    retryable: true
  };
}

/** Only http(s) may be opened, and http only for local dev — never
 * javascript:/data:/file: or a malformed URL. */
function isSafeOfficialUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:") return true;
    if (parsed.protocol === "http:") {
      return ["localhost", "127.0.0.1"].includes(parsed.hostname);
    }
    return false;
  } catch {
    return false;
  }
}

const PREP_STEPS = [
  "Preparing your verified profile",
  "Tailoring your resume",
  "Generating your cover letter",
  "Preparing saved answers",
  "Creating a secure application session"
];

/**
 * Assisted auto-apply preparation flow. Creates a secure application session,
 * shows what was prepared, then opens the official employer page and hands a
 * one-time launch token to the JobPilot extension. JobPilot never submits.
 */
export function AutoApplyModal({
  jobId,
  jobTitle,
  company,
  officialUrl,
  onClose
}: {
  jobId: number;
  jobTitle: string;
  company: string;
  officialUrl: string;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("preparing");
  const [session, setSession] = useState<CreatedApplicationSession | null>(null);
  const [error, setError] = useState<PreparationError | null>(null);
  const [extState, setExtState] = useState<ExtensionState | null>(null);
  const [popupBlocked, setPopupBlocked] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<LaunchError | null>(null);
  // Stable per-instance id tying this modal's staged launch payload to the Apply
  // button the extension's content script watches for (capture-phase click).
  // useId is pure and stable across renders — no impure call, no ref-in-render.
  const requestId = useId();
  // Guards against overlapping preparation requests (double-click / retry spam).
  const inFlight = useRef(false);

  const prepare = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const created = await createApplicationSession(jobId);
      setSession(created);
      setPhase("ready");
    } catch (err) {
      setError(toPreparationError(err));
      setPhase("error");
    } finally {
      inFlight.current = false;
    }
  }, [jobId]);

  useEffect(() => {
    let active = true;
    // Detect the extension (with version/capabilities) and keep watching so the
    // modal updates immediately if the user installs it while this is open.
    const refreshExt = () => void detectExtensionState().then((s) => active && setExtState(s));
    refreshExt();
    const onFocus = () => refreshExt();
    window.addEventListener("focus", onFocus);
    // Wrap in an async IIFE so the state updates happen behind an await boundary
    // (not synchronously within the effect body).
    void (async () => {
      await prepare();
    })();
    return () => {
      active = false;
      window.removeEventListener("focus", onFocus);
    };
  }, [prepare]);

  const extConnected = extState?.present === true && !extState.outdated;
  const extOutdated = extState?.present === true && extState.outdated;

  // Stage the launch payload with the extension as soon as it is prepared, so the
  // capture-phase click handler already has it when the user clicks Apply. The
  // token is staged into the extension's isolated content world, never the DOM.
  useEffect(() => {
    if (session && extConnected && requestId) {
      stageLaunch(requestId, session.extension_launch_token, session);
    }
  }, [session, extConnected, requestId]);

  function retry() {
    if (inFlight.current) return;
    setPhase("preparing"); // clears the error view and shows the loading checklist
    setError(null);
    void prepare();
  }

  // The URL to open: the backend-validated session URL when ready, else the
  // job's official URL (validated here) so preparation failure never blocks it.
  const manualUrl = session?.official_application_url ?? officialUrl;
  const canOpen = isSafeOfficialUrl(manualUrl);

  const buttonLabel = useMemo(() => {
    if (phase === "preparing") return "Preparing application…";
    if (launching) return "Opening application…";
    if (phase === "opened") return extConnected ? "Reopen application" : "Continue on official site";
    return extConnected ? "Open and autofill application" : "Open official application";
  }, [phase, launching, extConnected]);

  async function openSite() {
    if (!canOpen || launching) return;
    setLaunching(true);
    setLaunchError(null);
    // The extension owns tab creation and returns a real acknowledgement. Only
    // after that acknowledgement do we mark the server session opened.
    if (session && extConnected) {
      const result = await startAssistedApply(requestId, session.extension_launch_token, session);
      if (result.ok) {
        setPhase("opened");
        try {
          await api(`/application-sessions/${session.session_id}/status`, {
            method: "PATCH",
            body: JSON.stringify({ status: "opened" })
          });
        } catch {
          // Opening succeeded; status telemetry may be retried independently.
        }
      } else {
        setLaunchError({ code: result.code, message: result.message });
      }
      setLaunching(false);
      return;
    }
    // No extension (or outdated): fall back to opening the site manually.
    const win = openOfficialSite(manualUrl);
    if (win && session) {
      void api(`/application-sessions/${session.session_id}/status`, {
        method: "PATCH", body: JSON.stringify({ status: "opened" })
      }).catch(() => undefined);
    }
    setPopupBlocked(win === null);
    setPhase("opened");
    window.setTimeout(() => setLaunching(false), 1200);
  }

  return (
    <div
      className="assisted-application-backdrop fixed inset-0 z-50 p-4 sm:p-6"
      onClick={onClose}
      data-testid="assisted-application-backdrop"
    >
      <div
        className="assisted-application-dialog flex max-h-[min(88vh,780px)] w-full max-w-[610px] flex-col overflow-hidden rounded-[28px]"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-label="Assisted application"
        aria-modal="true"
      >
        <div className="assisted-application-glass-bar flex items-start justify-between gap-5 border-b border-line px-6 py-5 sm:px-7">
          <div className="min-w-0">
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-pine">Ready to apply</p>
            <h3 className="text-xl font-semibold tracking-[-0.025em] sm:text-2xl">Assisted application</h3>
            <p className="mt-1 truncate text-sm text-[var(--text-muted)]">{jobTitle} · {company}</p>
          </div>
          <button
            className="focus-ring grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line bg-[var(--glass-surface)] text-[var(--text-secondary)] transition hover:bg-panel hover:text-ink"
            type="button"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-[18px] w-[18px]" />
          </button>
        </div>

        <div className="flex-1 overflow-auto px-6 py-6 sm:px-7">
          {phase === "preparing" && <PreparingChecklist />}

          {phase === "error" && error && (
            <div className="rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-surface)] p-4 text-sm text-[var(--danger)]">
              <p className="font-semibold">We couldn’t prepare your application.</p>
              <p className="mt-1">{error.message}</p>
              {error.profileLink && (
                <a href="/profile" className="mt-2 inline-flex items-center gap-1 font-medium text-pine underline">
                  <ExternalLink className="h-3.5 w-3.5" /> Go to your profile
                </a>
              )}
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                {error.retryable
                  ? "You can retry, or open the official application and apply manually below."
                  : "You can still open the official application and apply manually below."}
              </p>
              {error.requestId && (
                <p className="mt-2 text-[11px] text-[var(--text-muted)]">Reference: {error.requestId}</p>
              )}
            </div>
          )}

          {session && phase !== "error" && (
            <div className="grid gap-5">
              <div className="grid gap-3 rounded-2xl border border-line bg-[var(--glass-surface)] p-4 shadow-sm">
                <ReadyRow icon={<FileText className="h-4 w-4" />} label="Tailored resume"
                          ok={session.resume.status === "ready"} />
                <ReadyRow icon={<Mail className="h-4 w-4" />} label="Tailored cover letter"
                          ok={session.cover_letter.status === "ready"} />
                <ReadyRow icon={<ShieldCheck className="h-4 w-4" />}
                          label={`${session.answers_available} verified answer${session.answers_available === 1 ? "" : "s"} ready to fill`}
                          ok />
                {session.review_required_count > 0 && (
                  <ReadyRow icon={<AlertTriangle className="h-4 w-4" />}
                            label={`${session.review_required_count} item${session.review_required_count === 1 ? "" : "s"} need your review`}
                            warn />
                )}
              </div>

              {session.warnings.length > 0 && (
                <ul className="rounded-2xl border border-[var(--warning-border)] bg-[var(--warning-surface)] p-4 text-xs text-[var(--warning)]">
                  {session.warnings.map((w) => (
                    <li key={w} className="list-inside list-disc">{w}</li>
                  ))}
                </ul>
              )}

              {extConnected && (
                <div className="rounded-2xl border border-[var(--success-border)] bg-[var(--success-surface)] p-4 text-xs text-[var(--accent)]">
                  <p className="flex items-center gap-1.5 font-medium text-[var(--accent)]">
                    <ShieldCheck className="h-4 w-4" /> JobPilot extension connected
                  </p>
                  <p className="mt-1 text-[var(--text-secondary)]">
                    We’ll open the application, fill verified fields, upload your tailored documents, and leave the final
                    review and submission to you.
                  </p>
                </div>
              )}

              {extOutdated && (
                <div className="rounded-2xl border border-[var(--warning-border)] bg-[var(--warning-surface)] p-4 text-xs text-[var(--warning)]">
                  <p className="font-medium">Your JobPilot extension needs an update.</p>
                  <p className="mt-1">
                    Reload JobPilot after installing or updating the extension — a JobPilot tab that was already
                    open when the extension last updated won&apos;t pick up the new version until it&apos;s
                    reloaded.{" "}
                    <a className="font-medium text-pine underline" href={EXTENSION_INSTALL_URL}
                       target="_blank" rel="noopener noreferrer">Update instructions</a>.
                  </p>
                </div>
              )}

              {extState?.present === false && (
                <div className="rounded-2xl border border-line bg-[var(--glass-surface)] p-4 text-xs text-[var(--text-muted)]">
                  <p className="font-medium text-[var(--text-secondary)]">
                    Install the JobPilot browser extension to automatically fill employer applications.
                  </p>
                  <p className="mt-1">
                    Without it you can still open the official application and apply manually below.{" "}
                    <a className="font-medium text-pine underline" href={EXTENSION_INSTALL_URL}
                       target="_blank" rel="noopener noreferrer">Install extension</a>. Already installed? Reload
                    this page — the extension may have been updated after this tab was opened.
                  </p>
                </div>
              )}

              {popupBlocked && canOpen && (
                <div className="rounded-2xl border border-[var(--warning-border)] bg-[var(--warning-surface)] p-4 text-xs text-[var(--warning)]">
                  Your browser blocked the new tab.{" "}
                  <a className="font-medium text-pine underline" href={manualUrl}
                     target="_blank" rel="noopener noreferrer">Open the official application manually</a>.
                </div>
              )}

              {launchError && (
                <div className="rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-surface)] p-4 text-xs text-[var(--danger)]" role="alert">
                  <p className="font-medium">Could not open the application automatically.</p>
                  <p className="mt-1">{launchError.message}</p>
                  <p className="mt-1 font-mono text-[11px]">{launchError.code}</p>
                </div>
              )}

              <p className="border-t border-line px-1 pt-5 text-xs leading-relaxed text-[var(--text-muted)]">
                Your application is prepared for review. JobPilot fills the form on the employer’s site but{" "}
                <span className="font-semibold">never submits it</span> — you review everything and click Submit yourself.
              </p>
            </div>
          )}
        </div>

        <div className="assisted-application-glass-bar flex flex-wrap items-center gap-2.5 border-t border-line px-6 py-4 sm:px-7 sm:py-5">
          {phase === "error" ? (
            <>
              {error?.retryable && (
                <Button type="button" onClick={retry}>
                  <RefreshCw className="h-4 w-4" /> Retry preparation
                </Button>
              )}
              {canOpen && (
                <Button variant="secondary" type="button" onClick={openSite}>
                  <ExternalLink className="h-4 w-4" /> Open official application
                </Button>
              )}
            </>
          ) : (
            <Button
              className="h-11 rounded-xl px-5 shadow-[0_8px_20px_rgb(31_94_69_/_18%)]"
              type="button"
              onClick={openSite}
              disabled={phase === "preparing" || !canOpen || launching}
            >
              {phase === "preparing" || launching
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <ExternalLink className="h-4 w-4" />}
              {buttonLabel}
            </Button>
          )}
          {extState?.present === false && phase !== "error" && (
            <a
              className="focus-ring inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm font-medium text-[var(--text-secondary)]"
              href={EXTENSION_INSTALL_URL} target="_blank" rel="noopener noreferrer"
            >
              <Download className="h-4 w-4" /> Install extension
            </a>
          )}
          <Button className="h-11 rounded-xl px-5" variant="secondary" type="button" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}

function PreparingChecklist() {
  const [activeStep, setActiveStep] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveStep((current) => Math.min(current + 1, PREP_STEPS.length - 1));
    }, 1800);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div>
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--success-surface)] text-pine">
          <Loader2 className="h-5 w-5 animate-spin" />
        </span>
        <div>
          <h4 className="font-semibold">Getting everything ready</h4>
          <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">
            We’re tailoring your documents and packaging verified answers for the employer’s form.
          </p>
        </div>
      </div>

      <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-panel" aria-hidden="true">
        <div
          className="h-full rounded-full bg-pine transition-[width] duration-700"
          style={{ width: `${Math.min(90, 12 + activeStep * 19)}%` }}
        />
      </div>

      <ul className="mt-4 grid gap-1.5" aria-live="polite">
        {PREP_STEPS.map((step, index) => {
          const complete = index < activeStep;
          const active = index === activeStep;
          return (
            <li
              key={step}
              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm ${
                active ? "bg-[var(--success-surface)] text-[var(--text-primary)]" : "text-[var(--text-muted)]"
              }`}
            >
              <span className={`grid h-5 w-5 place-items-center rounded-full ${complete ? "bg-pine text-white" : active ? "text-pine" : "border border-line"}`}>
                {complete ? <Check className="h-3 w-3" /> : active ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              </span>
              <span className={active ? "font-medium" : ""}>{step}{active ? "…" : ""}</span>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 text-xs text-[var(--text-muted)]">This usually takes less than a minute.</p>
    </div>
  );
}

function ReadyRow({ icon, label, ok, warn }: { icon: React.ReactNode; label: string; ok?: boolean; warn?: boolean }) {
  const tone = warn ? "text-[var(--warning)]" : ok ? "text-[var(--accent)]" : "text-[var(--text-muted)]";
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className={tone}>{icon}</span>
      <span className="flex-1 text-[var(--text-secondary)]">{label}</span>
      {ok && !warn && <Check className="h-4 w-4 text-[var(--success)]" />}
      {warn && <AlertTriangle className="h-4 w-4 text-[var(--warning)]" />}
    </div>
  );
}
