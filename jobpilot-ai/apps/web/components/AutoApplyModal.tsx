"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, Download, ExternalLink, FileText, Loader2, Mail, RefreshCw, ShieldCheck, X } from "lucide-react";
import { Button } from "@/components/Button";
import {
  type CreatedApplicationSession,
  type ExtensionState,
  createApplicationSession,
  detectExtensionState,
  handoffToExtension,
  openOfficialSite
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

  function openSite() {
    if (!canOpen || launching) return;
    setLaunching(true);
    // Hand the token to the extension BEFORE opening so it is ready when the
    // employer tab loads. The token never travels in the URL. Opening the site
    // does NOT mark the application complete — the user submits and confirms,
    // and we never claim the form was filled from here (only the extension can
    // report that, in its side panel on the employer tab).
    if (session && extConnected) {
      handoffToExtension(session.extension_launch_token, session);
    }
    if (session) {
      void api(`/application-sessions/${session.session_id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status: "opened" })
      }).catch(() => undefined);
    }
    const win = openOfficialSite(manualUrl);
    setPopupBlocked(win === null);
    setPhase("opened");
    // Brief "Opening application…" state; the real fill happens in the new tab.
    window.setTimeout(() => setLaunching(false), 1200);
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 p-4" onClick={onClose}>
      <div
        className="mx-auto mt-8 flex max-h-[88vh] w-full max-w-[560px] flex-col overflow-hidden rounded-2xl bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-label="Assisted application"
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-4">
          <div className="min-w-0">
            <h3 className="text-lg font-semibold">Assisted application</h3>
            <p className="mt-0.5 truncate text-sm text-[#5d675f]">{jobTitle} · {company}</p>
          </div>
          <button className="focus-ring rounded-md p-2" type="button" onClick={onClose} aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-auto px-6 py-5">
          {phase === "preparing" && <PreparingChecklist />}

          {phase === "error" && error && (
            <div className="rounded-lg border border-[#e7bcb1] bg-[#fbeeea] p-4 text-sm text-[#9f3d28]">
              <p className="font-semibold">We couldn’t prepare your application.</p>
              <p className="mt-1">{error.message}</p>
              {error.profileLink && (
                <a href="/profile" className="mt-2 inline-flex items-center gap-1 font-medium text-pine underline">
                  <ExternalLink className="h-3.5 w-3.5" /> Go to your profile
                </a>
              )}
              <p className="mt-2 text-xs text-[#5d675f]">
                {error.retryable
                  ? "You can retry, or open the official application and apply manually below."
                  : "You can still open the official application and apply manually below."}
              </p>
              {error.requestId && (
                <p className="mt-2 text-[11px] text-[#9a8f8b]">Reference: {error.requestId}</p>
              )}
            </div>
          )}

          {session && phase !== "error" && (
            <div className="grid gap-4">
              <div className="grid gap-2">
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
                <ul className="rounded-lg border border-[#ead191] bg-[#fff9e8] p-3 text-xs text-[#5b4a17]">
                  {session.warnings.map((w) => (
                    <li key={w} className="list-inside list-disc">{w}</li>
                  ))}
                </ul>
              )}

              {extConnected && (
                <div className="rounded-lg border border-[#bfe0cb] bg-[#eefaf1] p-3 text-xs text-[#215c40]">
                  <p className="flex items-center gap-1.5 font-medium text-[#215c40]">
                    <ShieldCheck className="h-4 w-4" /> JobPilot extension connected
                  </p>
                  <p className="mt-1 text-[#33403a]">
                    We’ll open the application, fill verified fields, upload your tailored documents, and leave the final
                    review and submission to you.
                  </p>
                </div>
              )}

              {extOutdated && (
                <div className="rounded-lg border border-[#ead191] bg-[#fff9e8] p-3 text-xs text-[#5b4a17]">
                  <p className="font-medium">Your JobPilot extension needs an update.</p>
                  <p className="mt-1">
                    Update the extension to autofill this application, or open it manually below.{" "}
                    <a className="font-medium text-pine underline" href={EXTENSION_INSTALL_URL}
                       target="_blank" rel="noopener noreferrer">Update instructions</a>.
                  </p>
                </div>
              )}

              {extState?.present === false && (
                <div className="rounded-lg border border-line bg-panel p-3 text-xs text-[#5d675f]">
                  <p className="font-medium text-[#33403a]">
                    Install the JobPilot browser extension to automatically fill employer applications.
                  </p>
                  <p className="mt-1">
                    Without it you can still open the official application and apply manually below.{" "}
                    <a className="font-medium text-pine underline" href={EXTENSION_INSTALL_URL}
                       target="_blank" rel="noopener noreferrer">Install extension</a>.
                  </p>
                </div>
              )}

              {popupBlocked && canOpen && (
                <div className="rounded-lg border border-[#ead191] bg-[#fff9e8] p-3 text-xs text-[#5b4a17]">
                  Your browser blocked the new tab.{" "}
                  <a className="font-medium text-pine underline" href={manualUrl}
                     target="_blank" rel="noopener noreferrer">Open the official application manually</a>.
                </div>
              )}

              <p className="rounded-lg bg-panel/60 p-3 text-xs text-[#5d675f]">
                Your application is prepared for review. JobPilot fills the form on the employer’s site but{" "}
                <span className="font-semibold">never submits it</span> — you review everything and click Submit yourself.
              </p>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-white px-6 py-4">
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
            <Button type="button" onClick={openSite} disabled={phase === "preparing" || !canOpen || launching}>
              {phase === "preparing" || launching
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <ExternalLink className="h-4 w-4" />}
              {buttonLabel}
            </Button>
          )}
          {extState?.present === false && phase !== "error" && (
            <a
              className="focus-ring inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm font-medium text-[#33403a]"
              href={EXTENSION_INSTALL_URL} target="_blank" rel="noopener noreferrer"
            >
              <Download className="h-4 w-4" /> Install extension
            </a>
          )}
          {canOpen && phase !== "error" && (
            <a className="text-sm text-[#5d675f] underline" href={manualUrl}
               target="_blank" rel="noopener noreferrer">Open manually</a>
          )}
          <Button variant="secondary" type="button" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}

function PreparingChecklist() {
  return (
    <ul className="grid gap-2.5">
      {PREP_STEPS.map((step) => (
        <li key={step} className="flex items-center gap-3 text-sm text-[#33403a]">
          <Loader2 className="h-4 w-4 animate-spin text-pine" />
          {step}…
        </li>
      ))}
    </ul>
  );
}

function ReadyRow({ icon, label, ok, warn }: { icon: React.ReactNode; label: string; ok?: boolean; warn?: boolean }) {
  const tone = warn ? "text-[#8a5312]" : ok ? "text-[#215c40]" : "text-[#5d675f]";
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className={tone}>{icon}</span>
      <span className="flex-1 text-[#33403a]">{label}</span>
      {ok && !warn && <Check className="h-4 w-4 text-[#2f8f5b]" />}
      {warn && <AlertTriangle className="h-4 w-4 text-[#e0872f]" />}
    </div>
  );
}
