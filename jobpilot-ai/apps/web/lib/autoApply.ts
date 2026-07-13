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
// --------------------------------------------------------------------------- //
const WEB_SOURCE = "jobpilot-web";
const EXT_SOURCE = "jobpilot-extension";

/** Lowest extension protocol version this web build can talk to. Bump alongside
 * the extension's PROTOCOL_VERSION when the message contract changes. */
export const MIN_EXTENSION_PROTOCOL = 1;

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

/** Ping the extension and return its rich info, or null if none replies in time.
 * The web app records real capabilities instead of merely guessing presence. */
export function detectExtensionInfo(timeoutMs = 800): Promise<ExtensionInfo | null> {
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
      if (event.source === window && data?.source === EXT_SOURCE && data.type === "PONG") {
        // Tolerate an older extension that PONGs without an info payload.
        finish(
          data.info ?? { installed: true, version: "0.0.0", protocolVersion: 0, capabilities: [] }
        );
      }
    };
    window.addEventListener("message", onMessage);
    window.postMessage({ source: WEB_SOURCE, type: "PING" } satisfies ExtMessage, window.location.origin);
    window.setTimeout(() => finish(null), timeoutMs);
  });
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
 * Hand the one-time launch token to the extension. The extension content script
 * (running on the JobPilot origin) forwards it to its background worker, which
 * exchanges it for a session-scoped token when the employer tab opens.
 */
export function handoffToExtension(launchToken: string, session: ApplicationSessionView): void {
  if (typeof window === "undefined") {
    return;
  }
  window.postMessage(
    {
      source: WEB_SOURCE,
      type: "LAUNCH",
      payload: {
        launchToken,
        sessionId: session.session_id,
        officialUrl: session.official_application_url,
        atsType: session.ats_type
      }
    } satisfies ExtMessage,
    window.location.origin
  );
}

/** Open the employer application page in a new tab. Returns null if blocked. */
export function openOfficialSite(url: string): Window | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.open(url, "_blank", "noopener,noreferrer");
}
