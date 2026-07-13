/**
 * MV3 service worker: secure launch->session handoff and side-panel control.
 *
 * The web app hands a one-time launch token to the JobPilot content script,
 * which forwards it here. When the employer tab opens and asks for its session,
 * we exchange the launch token for a session-scoped token (stored only in
 * extension-isolated session storage) and return the session data. Tokens never
 * touch the employer page or any page-local storage.
 */

import { completeSession, exchangeLaunchToken, fetchSessionData, postEvent, reportAutofillResult } from "./api/client";
import { getApiBase } from "./config";
import type { AutofillResult, LaunchPayload, RuntimeMessage } from "./messages";
import type { ApplicationSessionData } from "./types";

type Pending = LaunchPayload & { host: string };
type SessionEntry = { token: string; data: ApplicationSessionData };

const PENDING_KEY = "pendingLaunches";
const SESSION_KEY = "activeSessions";

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => undefined);
});

chrome.runtime.onMessage.addListener((message: RuntimeMessage, sender, sendResponse) => {
  if (message.type === "LAUNCH_HANDOFF") {
    void storePending(message.payload).then(() => {
      if (sender.tab?.windowId != null) {
        void chrome.sidePanel?.open?.({ windowId: sender.tab.windowId }).catch(() => undefined);
      }
      sendResponse({ ok: true });
    });
    return true;
  }
  if (message.type === "REQUEST_SESSION") {
    void resolveSession(message.url)
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err: unknown) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  if (message.type === "REQUEST_DOCUMENT") {
    void fetchDocument(message.sessionId, message.kind)
      .then((doc) => sendResponse({ ok: true, ...doc }))
      .catch((err: unknown) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  if (message.type === "AUDIT_EVENT") {
    void auditEvent(message.sessionId, message.action_type, message.field_key, message.status)
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (message.type === "REPORT_RESULTS") {
    void recordResults(message.sessionId, message.result)
      .then(() => sendResponse({ ok: true }))
      .catch((err: unknown) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  if (message.type === "COMPLETE_SESSION") {
    void completeActive(message.sessionId)
      .then(() => sendResponse({ ok: true }))
      .catch((err: unknown) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  return undefined;
});

async function recordResults(sessionId: number, result: AutofillResult): Promise<void> {
  const active = await getActiveById(sessionId);
  if (!active) {
    throw new Error("No active session token");
  }
  await reportAutofillResult(active.token, sessionId, result);
}

async function completeActive(sessionId: number): Promise<void> {
  const active = await getActiveById(sessionId);
  if (!active) {
    throw new Error("No active session token");
  }
  await completeSession(active.token, sessionId);
}

async function getActiveById(sessionId: number): Promise<SessionEntry | null> {
  const store = await chrome.storage.session.get(SESSION_KEY);
  const map: Record<string, SessionEntry> = store[SESSION_KEY] || {};
  return map[String(sessionId)] ?? null;
}

async function fetchDocument(
  sessionId: number,
  kind: "resume" | "cover-letter"
): Promise<{ dataUrl: string; filename: string }> {
  const active = await getActiveById(sessionId);
  if (!active) {
    throw new Error("No active session token");
  }
  const base = await getApiBase();
  const res = await fetch(`${base}/application-sessions/${sessionId}/${kind}?fmt=pdf`, {
    headers: { Authorization: `Bearer ${active.token}` }
  });
  if (!res.ok) {
    throw new Error(`Document fetch failed (${res.status})`);
  }
  const buffer = await res.arrayBuffer();
  const filename = `${kind}.pdf`;
  return { dataUrl: `data:application/pdf;base64,${toBase64(buffer)}`, filename };
}

async function auditEvent(sessionId: number, actionType: string, fieldKey?: string, status?: string): Promise<void> {
  const active = await getActiveById(sessionId);
  if (!active) return;
  await postEvent(active.token, sessionId, { action_type: actionType, field_key: fieldKey, status });
}

function toBase64(buffer: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

async function storePending(payload: LaunchPayload): Promise<void> {
  const host = safeHost(payload.officialUrl);
  const store = await chrome.storage.session.get(PENDING_KEY);
  const pending: Pending[] = Array.isArray(store[PENDING_KEY]) ? store[PENDING_KEY] : [];
  // Keep only the most recent few pending launches.
  const next = [{ ...payload, host }, ...pending.filter((p) => p.sessionId !== payload.sessionId)].slice(0, 5);
  await chrome.storage.session.set({ [PENDING_KEY]: next });
}

async function resolveSession(url: string): Promise<ApplicationSessionData | null> {
  const host = safeHost(url);
  const active = await getActiveForHost(host);
  if (active) {
    return active.data;
  }
  const store = await chrome.storage.session.get(PENDING_KEY);
  const pending: Pending[] = Array.isArray(store[PENDING_KEY]) ? store[PENDING_KEY] : [];
  const match = pending.find((p) => host && (host.includes(p.host) || p.host.includes(host)));
  if (!match) {
    return null;
  }
  const { session_token } = await exchangeLaunchToken(match.launchToken);
  const data = await fetchSessionData(session_token, match.sessionId);
  await setActive(match.sessionId, { token: session_token, data });
  await clearPending(match.sessionId);
  return data;
}

async function getActiveForHost(host: string): Promise<SessionEntry | null> {
  const store = await chrome.storage.session.get(SESSION_KEY);
  const map: Record<string, SessionEntry> = store[SESSION_KEY] || {};
  for (const entry of Object.values(map)) {
    if (host && safeHost(entry.data.officialUrl).includes(host)) {
      return entry;
    }
  }
  return null;
}

async function setActive(sessionId: number, entry: SessionEntry): Promise<void> {
  const store = await chrome.storage.session.get(SESSION_KEY);
  const map: Record<string, SessionEntry> = store[SESSION_KEY] || {};
  map[String(sessionId)] = entry;
  await chrome.storage.session.set({ [SESSION_KEY]: map });
}

async function clearPending(sessionId: number): Promise<void> {
  const store = await chrome.storage.session.get(PENDING_KEY);
  const pending: Pending[] = Array.isArray(store[PENDING_KEY]) ? store[PENDING_KEY] : [];
  await chrome.storage.session.set({ [PENDING_KEY]: pending.filter((p) => p.sessionId !== sessionId) });
}

function safeHost(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}
