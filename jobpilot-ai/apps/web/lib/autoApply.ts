import { api } from "@/lib/api";

/** A prepared assisted-apply session as returned by the backend. */
export type ApplicationSessionView = {
  session_id: number;
  status: string;
  official_application_url: string;
  ats_type: string | null;
  job: { id: number; title: string | null; company: string | null; location: string | null };
  resume: { status: string; document_id: number | null; download_url: string | null };
  cover_letter: { status: string; document_id: number | null; download_url: string | null };
  answers_available: number;
  review_required_count: number;
  unresolved_questions: { canonical_key: string; reason?: string }[];
  warnings: string[];
  created_at?: string;
  expires_at?: string | null;
  completed_at?: string | null;
};

export type CreatedApplicationSession = ApplicationSessionView & { extension_launch_token: string };

export async function createApplicationSession(jobId: number): Promise<CreatedApplicationSession> {
  return api<CreatedApplicationSession>("/application-sessions", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId })
  });
}

export async function getApplicationSession(sessionId: number): Promise<ApplicationSessionView> {
  return api<ApplicationSessionView>(`/application-sessions/${sessionId}`);
}

// --------------------------------------------------------------------------- //
// Extension handoff (window.postMessage handshake — no real extension id needed,
// no token ever placed in a URL or exposed to the employer page).
//
// These message-type strings and the payload shape MUST match the extension's
// src/messages.ts (MSG.PING / MSG.PONG / MSG.STAGE_LAUNCH, PROTOCOL_VERSION 2).
// The two builds are separate packages, so the contract is mirrored here.
// --------------------------------------------------------------------------- //
const WEB_SOURCE = "jobpilot-web";
const EXT_SOURCE = "jobpilot-extension";
const MSG_PING = "JOBPILOT_PING";
const MSG_PONG = "JOBPILOT_PONG";
const MSG_STAGE_LAUNCH = "JOBPILOT_STAGE_LAUNCH";
const MSG_START_ASSISTED_APPLY = "JOBPILOT_START_ASSISTED_APPLY";
const MSG_START_ASSISTED_APPLY_RESULT = "JOBPILOT_START_ASSISTED_APPLY_RESULT";

/** Lowest extension protocol version this web build can talk to. Bump alongside
 * the extension's PROTOCOL_VERSION when the message contract changes. */
export const MIN_EXTENSION_PROTOCOL = 3;

type ExtMessage = { source: string; type: string; [key: string]: unknown };

/** The handshake reply from the installed extension. */
export type ExtensionInfo = {
  installed: true;
  version: string;
  protocolVersion: number;
  capabilities: string[];
};

/** Resolved extension state for the UI. `null` = not detected. `outdated` is
 * true when the extension is present but speaks an older protocol. */
export type ExtensionState =
  | { present: false }
  | { present: true; outdated: boolean; info: ExtensionInfo };

/** One PING attempt; resolves with the extension's info, or null if nothing
 * replies within `timeoutMs`. */
function pingOnce(timeoutMs: number): Promise<ExtensionInfo | null> {
  if (typeof window === "undefined") {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: ExtensionInfo | null) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      resolve(value);
    };
    const onMessage = (event: MessageEvent) => {
      const data = event.data as (ExtMessage & { info?: ExtensionInfo }) | undefined;
      if (event.source === window && data?.source === EXT_SOURCE && data.type === MSG_PONG) {
        // Tolerate an older extension that PONGs without an info payload.
        finish(
          data.info ?? { installed: true, version: "0.0.0", protocolVersion: 0, capabilities: [] }
        );
      }
    };
    window.addEventListener("message", onMessage);
    window.postMessage({ source: WEB_SOURCE, type: MSG_PING } satisfies ExtMessage, window.location.origin);
    window.setTimeout(() => finish(null), timeoutMs);
  });
}

/** Ping the extension and return its rich info, or null if none replies.
 * The content script may still be initializing (e.g. it runs at
 * document_idle and can race the page's own React hydration), so a single
 * missed PING is not conclusive — retry once, briefly, before concluding the
 * extension truly isn't there. The web app records real capabilities instead
 * of merely guessing presence. */
export async function detectExtensionInfo(timeoutMs = 800): Promise<ExtensionInfo | null> {
  const first = await pingOnce(timeoutMs);
  if (first) return first;
  return pingOnce(timeoutMs);
}

/** Resolve the extension state (present / outdated) for the modal. */
export async function detectExtensionState(timeoutMs = 800): Promise<ExtensionState> {
  const info = await detectExtensionInfo(timeoutMs);
  if (!info) {
    return { present: false };
  }
  return { present: true, outdated: info.protocolVersion < MIN_EXTENSION_PROTOCOL, info };
}

/** Back-compat boolean check. */
export async function detectExtension(timeoutMs = 800): Promise<boolean> {
  return (await detectExtensionInfo(timeoutMs)) !== null;
}

/**
 * STAGE the one-time launch token with the extension's JobPilot-origin content
 * script (into its isolated world — never left in the DOM). The extension does
 * NOT act yet: it waits for the real Apply-button click (handled in the content
 * script's capture phase) so it can open the side panel + employer tab inside a
 * valid user gesture. `requestId` ties the staged payload to the clicked button.
 */
function launchPayload(requestId: string, launchToken: string, session: ApplicationSessionView) {
  return {
    requestId,
    launchToken,
    sessionId: session.session_id,
    jobId: session.job.id,
    officialUrl: session.official_application_url,
    atsType: session.ats_type
  };
}

/** Persist a prepared handoff before any navigation. This makes the manual-link
 * fallback autofill-capable when the ATS content script subsequently starts. */
export function stageLaunch(requestId: string, launchToken: string, session: ApplicationSessionView): void {
  if (typeof window === "undefined") {
    return;
  }
  window.postMessage(
    {
      source: WEB_SOURCE,
      type: MSG_STAGE_LAUNCH,
      payload: launchPayload(requestId, launchToken, session)
    } satisfies ExtMessage,
    window.location.origin
  );
}

export type LaunchAcknowledgement =
  | { ok: true; applicationId: string; tabId: number }
  | { ok: false; code: string; message: string };

/** Start the primary flow and resolve only after the background confirms the
 * durable handoff and employer tab. Preparation alone is never reported as an
 * opened application. */
export function startAssistedApply(
  requestId: string,
  launchToken: string,
  session: ApplicationSessionView,
  timeoutMs = 5000
): Promise<LaunchAcknowledgement> {
  if (typeof window === "undefined") {
    return Promise.resolve({ ok: false, code: "EXTENSION_UNAVAILABLE", message: "Extension bridge is unavailable." });
  }
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: LaunchAcknowledgement) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      resolve(result);
    };
    const onMessage = (event: MessageEvent) => {
      const data = event.data as ExtMessage | undefined;
      if (event.source !== window || data?.source !== EXT_SOURCE || data.type !== MSG_START_ASSISTED_APPLY_RESULT || data.requestId !== requestId) return;
      finish(data.result as LaunchAcknowledgement);
    };
    window.addEventListener("message", onMessage);
    window.postMessage({
      source: WEB_SOURCE,
      type: MSG_START_ASSISTED_APPLY,
      payload: launchPayload(requestId, launchToken, session)
    } satisfies ExtMessage, window.location.origin);
    window.setTimeout(() => finish({ ok: false, code: "EXTENSION_NO_ACK", message: "The extension did not acknowledge the handoff. Reload it and try again." }), timeoutMs);
  });
}

/** Open the employer application page in a new tab (manual fallback ONLY — the
 * primary extension flow lets the background create the tab so it gets the exact
 * tab id and avoids the popup blocker). Returns null if blocked. */
export function openOfficialSite(url: string): Window | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.open(url, "_blank", "noopener,noreferrer");
}
