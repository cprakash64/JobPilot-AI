/**
 * MV3 service worker: the canonical launch state machine.
 *
 * Responsibilities:
 *   • Accept an acknowledged, versioned handoff from the JobPilot bridge,
 *     persist it before navigation, then create/focus the employer tab and bind it
 *     exact tab id in chrome.storage.session.
 *   • Drive a pull-based readiness handshake with the employer content script
 *     (ping → inject fallback → retry with backoff).
 *   • Exchange the single-use launch token exactly once and cache the resulting
 *     session package per tab so refreshes/retries never re-consume the token.
 *   • Never hold important state only in worker memory; never submit anything.
 */

import {
  ApiError,
  completeSession,
  confirmSessionName,
  exchangeLaunchToken,
  fetchSessionData,
  postEvent,
  reportAutofillResult,
  saveSessionAnswer
} from "./api/client";
import { getApiBase, JOBPILOT_WEB_ORIGINS } from "./config";
import { lastError, log } from "./logger";
import {
  MSG,
  PROTOCOL_VERSION,
  parseRuntimeMessage,
  type AutofillReason,
  type AutofillResult,
  type LaunchPayload,
  type PendingLaunch,
  type ProgressPayload
} from "./messages";
import {
  clearTab,
  cleanupExpired,
  findPendingByApplication,
  getActive,
  getPackage,
  getPending,
  getView,
  initialView,
  patchView,
  putPackage,
  putActive,
  putPending,
  putView,
  updatePending,
  type SessionPackage
} from "./state";
import { urlsMatchForHandoff } from "./url";

const LAUNCH_TTL_MS = 15 * 60 * 1000;
const READY_MAX_ATTEMPTS = 6;
const packageLoads = new Map<number, Promise<SessionPackage>>();
void cleanupExpired();

const RUNTIME_REVIVAL_KEY = "jobpilotRuntimeRevivedV1";

// `runtime.onInstalled` is not guaranteed for Developer Mode's Reload button.
// storage.session survives ordinary MV3 worker suspension but is reset with the
// extension runtime, so this runs once after a real extension-context reset —
// not every time the service worker merely wakes up.
void reviveAfterRuntimeReset();

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => undefined);
  void reviveOpenJobPilotTabs();
});

/**
 * A JobPilot tab that was already open when the extension was installed or
 * reloaded has an orphaned content script — `chrome.runtime` in that isolated
 * world is invalidated, so it can never bridge to this (new) background no
 * matter how long the page waits. Re-inject the (ATS-only-code-free)
 * web-origin bridge into every already-open, approved JobPilot tab so the
 * user doesn't have to know to manually refresh. Never touches ATS/employer
 * tabs — only the exact origins the bridge role is allowed to run on.
 */
async function reviveOpenJobPilotTabs(): Promise<void> {
  for (const origin of JOBPILOT_WEB_ORIGINS) {
    let tabs: chrome.tabs.Tab[] = [];
    try {
      tabs = await chrome.tabs.query({ url: `${origin}/*` });
    } catch {
      continue;
    }
    for (const tab of tabs) {
      if (tab.id == null) continue;
      try {
        await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
        log.debug("bridge revived on existing tab", { tabId: tab.id });
      } catch (err) {
        // Restricted tab (chrome://, a page still loading, etc.) — nothing
        // to do; the user can still reload it manually.
        log.debug("bridge revive skipped", { tabId: tab.id, reason: String(err).slice(0, 40) });
      }
    }
  }
}

async function reviveAfterRuntimeReset(): Promise<void> {
  try {
    const stored = await chrome.storage.session.get(RUNTIME_REVIVAL_KEY);
    if (stored[RUNTIME_REVIVAL_KEY]) return;
    await chrome.storage.session.set({ [RUNTIME_REVIVAL_KEY]: Date.now() });
  } catch {
    // Older/fake Chrome environments without storage.session still get the
    // safe best-effort revival.
  }

  await reviveOpenJobPilotTabs();

  // If an employer tab was already open, its old content script and widget are
  // just as stale as the JobPilot bridge. Durable local state contains the
  // exact bound tab, so revive only that user-authorized application tab.
  const active = await getActive().catch(() => null);
  if (
    active?.targetTabId != null &&
    active.expiresAt > Date.now()
  ) {
    await ensureContentReady(active.targetTabId).catch(() => false);
  }
}

// Keep the durable handoff when a tab closes so "Open manually" and reopen can
// bind a new ATS tab. Only discard tab-specific view/package records.
chrome.tabs.onRemoved.addListener((tabId) => {
  clearFrameRegistry(tabId);
  void (async () => {
    const pending = await getPending(tabId);
    if (pending) await putActive({ ...pending, targetTabId: undefined, status: "prepared", state: "package_ready" });
    await clearTab(tabId);
  })();
});

// When a tab with a pending launch finishes loading (initial load, refresh, or
// SPA navigation reported as complete), make sure the content script is ready.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== "complete") return;
  // A top-frame navigation invalidates every frame in the tab.
  if (changeInfo.url) clearFrameRegistry(tabId);
  void (async () => {
    const pending = await getPending(tabId);
    if (!pending) return;
    await ensureContentReady(tabId).catch(() => undefined);
  })();
});

// --------------------------------------------------------------------------- //
// Message router (validates every message; unknown → structured error)
// --------------------------------------------------------------------------- //
chrome.runtime.onMessage.addListener((raw, sender, sendResponse) => {
  const message = parseRuntimeMessage(raw);
  if (!message) {
    sendResponse({ ok: false, error: "UNKNOWN_MESSAGE" });
    return false;
  }

  switch (message.type) {
    case MSG.HANDSHAKE:
      sendResponse({ ok: message.protocolVersion === PROTOCOL_VERSION, protocolVersion: PROTOCOL_VERSION });
      return false;

    case MSG.STAGE_LAUNCH:
      void stageHandoff(message.payload)
        .then(() => sendResponse({ ok: true, applicationId: String(message.payload.sessionId) }))
        .catch((err) => sendResponse({ ok: false, code: "INVALID_HANDOFF", message: safeMessage(err) }));
      return true;

    case MSG.LAUNCH_REQUEST:
      handleLaunchRequest(message.payload, sender, sendResponse);
      return true;

    case MSG.CONTENT_READY:
      if (sender.tab?.id != null && sender.frameId != null && message.probe) {
        registerFrameProbe(sender.tab.id, sender.frameId, message.probe);
      }
      void handleContentReady(sender, message.url, message.isTopFrame, sendResponse);
      return true;

    case MSG.GET_PENDING_LAUNCH:
      void handleGetPending(sender, sendResponse);
      return true;

    case MSG.AUTOFILL_PROGRESS:
      void applyProgress(sender.tab?.id, message.payload).then(() => sendResponse({ ok: true }));
      return true;

    case MSG.AUTOFILL_RESULT:
      void recordResult(sender.tab?.id, message.sessionId, message.result, message.progress)
        .then(() => sendResponse({ ok: true }))
        .catch((err) => sendResponse({ ok: false, error: String(err) }));
      return true;

    case MSG.AUTOFILL_FAILED:
      // sender.frameId is Chrome-supplied and therefore trustworthy; a frameId
      // in the message body would be forgeable by a compromised page.
      void applyFailure(sender.tab?.id, message.reasonCode, message.message, sender.frameId).then(() =>
        sendResponse({ ok: true })
      );
      return true;

    case MSG.REQUEST_DOCUMENT:
      void fetchDocument(sender.tab?.id, message.sessionId, message.kind)
        .then((doc) => sendResponse({ ok: true, ...doc }))
        .catch((err) => sendResponse({ ok: false, error: String(err) }));
      return true;

    case MSG.AUDIT_EVENT:
      void auditEvent(sender.tab?.id, message.sessionId, message.action_type, message.field_key, message.status)
        .then(() => sendResponse({ ok: true }))
        .catch(() => sendResponse({ ok: false }));
      return true;

    case MSG.START_AUTOFILL:
      void startAutofillForTab(message.tabId, message.reason)
        .then((r) => sendResponse(r))
        .catch((err) => sendResponse({ ok: false, error: String(err) }));
      return true;

    case MSG.CLEAR_SESSION:
      void clearSession(message.tabId).then(() => sendResponse({ ok: true }));
      return true;

    case MSG.COMPLETE_SESSION:
      void completeActive(message.sessionId)
        .then(() => sendResponse({ ok: true }))
        .catch((err) => sendResponse({ ok: false, error: String(err) }));
      return true;

    case MSG.GET_VIEW_STATE:
      void resolveViewTab(message.tabId)
        .then((id) => getView(id ?? -1))
        .then((v) => sendResponse({ ok: true, view: v }));
      return true;

    case MSG.SAVE_ANSWER:
      void handleSaveAnswer(sender.tab?.id, message)
        .then(() => sendResponse({ ok: true }))
        .catch((err) => sendResponse({ ok: false, error: safeMessage(err) }));
      return true;

    case MSG.CONFIRM_NAME:
      void handleConfirmName(sender.tab?.id, message)
        .then(() => sendResponse({ ok: true }))
        .catch((err) => sendResponse({ ok: false, error: safeMessage(err) }));
      return true;

    default:
      sendResponse({ ok: false, error: "UNKNOWN_MESSAGE" });
      return false;
  }
});

// --------------------------------------------------------------------------- //
// Launch (user gesture)
// --------------------------------------------------------------------------- //
function handleLaunchRequest(
  payload: LaunchPayload,
  sender: chrome.runtime.MessageSender,
  sendResponse: (r: unknown) => void
): void {
  const windowId = sender.tab?.windowId;
  void (async () => {
    try {
      validatePayload(payload);
      await ensureTargetAccess(payload.officialUrl);
      const applicationId = String(payload.sessionId);
      const existing = await findPendingByApplication(applicationId);
      if (existing) {
        const tab = await chrome.tabs.get(existing.tabId).catch(() => null);
        if (tab) {
          await chrome.tabs.update(existing.tabId, { active: true, url: payload.officialUrl });
          if (tab.windowId != null) await chrome.windows.update(tab.windowId, { focused: true }).catch(() => undefined);
          await updatePending(existing.tabId, {
            ...handoffFields(payload), targetTabId: existing.tabId, status: "opening", state: "waiting_for_tab"
          });
          void ensureContentReady(existing.tabId);
          sendResponse({ ok: true, type: MSG.LAUNCH_ACCEPTED, applicationId, tabId: existing.tabId });
          return;
        }
      }
      // Persist first: navigation must never be the only owner of the handoff.
      const pending = await stageHandoff(payload, "opening");
      const created = await chrome.tabs.create({
        url: payload.officialUrl,
        windowId: windowId ?? undefined,
        active: true
      });
      const tabId = created.id;
      if (tabId == null) throw new Error("no tab id");

      const bound = { ...pending, targetTabId: tabId, status: "opening" as const, state: "waiting_for_tab" as const };
      await putPending(tabId, bound);
      await putView(tabId, initialView(tabId, bound, null, null));
      log.info("launch accepted", { requestId: payload.requestId, tabId, origin: pending.expectedOrigin });
      sendResponse({ ok: true, type: MSG.LAUNCH_ACCEPTED, applicationId, tabId });
    } catch (err) {
      log.error("launch failed", { requestId: payload.requestId, reason: "open_or_create" });
      sendResponse({ ok: false, type: MSG.LAUNCH_FAILED, code: "TAB_OPEN_FAILED", message: safeMessage(err) });
    }
  })();
}

async function stageHandoff(payload: LaunchPayload, status: PendingLaunch["status"] = "prepared"): Promise<PendingLaunch> {
  validatePayload(payload);
  const previous = await getActive();
  const createdAt = previous?.applicationId === String(payload.sessionId) ? previous.createdAt : Date.now();
  const launch: PendingLaunch = {
    ...handoffFields(payload), createdAt, expiresAt: Date.now() + LAUNCH_TTL_MS,
    targetTabId: previous?.applicationId === String(payload.sessionId) ? previous.targetTabId : undefined,
    status, state: status === "prepared" ? "package_ready" : "opening_tab"
  };
  await putActive(launch);
  log.info("handoff saved", { requestId: payload.requestId, sessionId: payload.sessionId, state: status });
  return launch;
}

function handoffFields(payload: LaunchPayload) {
  return {
    version: 1 as const, applicationId: String(payload.sessionId), jobId: String(payload.jobId),
    applicationUrl: payload.officialUrl, handoffToken: payload.launchToken,
    requestId: payload.requestId, sessionId: payload.sessionId, launchToken: payload.launchToken,
    officialUrl: payload.officialUrl, expectedOrigin: safeOrigin(payload.officialUrl),
    protocolVersion: PROTOCOL_VERSION, atsType: payload.atsType
  };
}

function validatePayload(payload: LaunchPayload): void {
  if (!payload || !payload.requestId || !payload.launchToken || !Number.isInteger(payload.sessionId) || !Number.isInteger(payload.jobId)) {
    throw new Error("Required handoff fields are missing");
  }
  const parsed = new URL(payload.officialUrl);
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && ["localhost", "127.0.0.1"].includes(parsed.hostname))) {
    throw new Error("Application URL is unsupported");
  }
}

/**
 * With host_permissions statically covering https://*, http://localhost/* and
 * http://127.0.0.1/*, this should always pass — chrome.permissions.request()
 * requires an active user gesture and is fragile when called from deep inside
 * an async message chain (the original cause of "Chrome blocked the tab"
 * failures on employer-hosted domains). Kept only as a defensive fallback for
 * a user who has manually revoked broad site access via chrome://extensions.
 */
async function ensureTargetAccess(url: string): Promise<void> {
  const origin = `${new URL(url).origin}/*`;
  if (await chrome.permissions.contains({ origins: [origin] })) return;
  const granted = await chrome.permissions.request({ origins: [origin] }).catch(() => false);
  if (!granted) throw new Error("HOST_PERMISSION_MISSING");
}

// --------------------------------------------------------------------------- //
// Readiness handshake (pull-based)
// --------------------------------------------------------------------------- //
/**
 * The single gate every ATS-page content script frame must pass before it is
 * allowed to touch the DOM. Because host_permissions now cover every https(s)
 * origin (employer/ATS forms live on arbitrary domains we cannot enumerate in
 * advance), the content script is declaratively injected into every page —
 * this is what keeps it dormant everywhere except a user-initiated handoff.
 *
 * A tab is "matched" once ANY frame in it (top frame, almost always) reports a
 * URL that matches the active handoff via urlsMatchForHandoff. After that, the
 * whole TAB is trusted (targetTabId binding) so nested ATS iframes — whose own
 * URL rarely resembles the employer page URL — are also allowed to scan/fill.
 * The top frame alone is re-validated against the handoff URL on every call so
 * a tab that has since navigated away never keeps auto-filling.
 */
async function handleContentReady(
  sender: chrome.runtime.MessageSender,
  url: string,
  isTopFrame: boolean,
  sendResponse: (r: unknown) => void
): Promise<void> {
  const tabId = sender.tab?.id;
  if (tabId == null) {
    sendResponse({ ok: false, matched: false, error: "NO_TAB", launch: null });
    return;
  }
  const frameId = sender.frameId;
  let pending = await getPending(tabId);
  if (!pending) {
    const active = await getActive();
    if (!active) {
      sendResponse({ ok: true, matched: false, error: "HANDOFF_NOT_FOUND", launch: null });
      return;
    }
    if (Date.now() > active.expiresAt) {
      sendResponse({ ok: true, matched: false, error: "HANDOFF_EXPIRED", launch: null });
      return;
    }
    // A frame binds the tab either by its own URL or (for cases where the
    // handoff URL is the top page but this is the first frame to report in)
    // the tab's own top-level URL.
    const candidateUrls = [url, sender.tab?.url].filter((u): u is string => Boolean(u));
    const matches = candidateUrls.some((u) => urlsMatchForHandoff(active.applicationUrl, u));
    if (!matches) {
      log.debug("handoff url mismatch", { tabId, frameId: frameId ?? -1, isTopFrame });
      sendResponse({ ok: true, matched: false, error: "HANDOFF_URL_MISMATCH", launch: null });
      return;
    }
    pending = { ...active, targetTabId: tabId, status: "detecting", state: "detecting_ats" };
    await putPending(tabId, pending);
    await putView(tabId, initialView(tabId, pending, null, null));
  }
  const reject = validateLaunch(pending, url, tabId, isTopFrame);
  if (reject) {
    // The tab WAS bound to a handoff, so surface the sanitized launch + a
    // specific error rather than pretending nothing matched — the widget can
    // then show a meaningful failure instead of staying silently blank.
    await applyFailure(tabId, reject);
    sendResponse({ ok: false, matched: true, error: reject, launch: sanitize(pending) });
    return;
  }
  log.info("content script ready", { tabId, frameId: frameId ?? -1, isTopFrame });
  await patchView(tabId, { contentReady: true, state: "fetching_package" });
  try {
    const pkg = await ensurePackage(tabId, pending);
    await patchView(tabId, {
      packageLoaded: true,
      company: pkg.session.company,
      jobTitle: pkg.session.jobTitle,
      sessionId: pkg.session.sessionId
    });
    // Hand the content script the meta + session so it can autofill immediately.
    sendResponse({ ok: true, matched: true, launch: sanitize(pending), session: pkg.session, reason: "automatic_launch" as AutofillReason });
  } catch (err) {
    const code = classifyPackageError(err);
    await applyFailure(tabId, code);
    sendResponse({ ok: false, matched: true, error: code, launch: sanitize(pending) });
  }
}

/** Classify a session-package load failure precisely instead of the generic
 * "couldn't be loaded" — surfaced verbatim to the widget (dev-safe: a status
 * code and a stable machine code only, never a response body). */
function classifyPackageError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "SESSION_UNAUTHORIZED";
    if (err.status === 404) return "SESSION_NOT_FOUND";
    if (err.status === 410) return "HANDOFF_EXPIRED";
    return "SESSION_PACKAGE_FAILED";
  }
  if (String(err).includes("already used")) return "TOKEN_CONSUMED";
  return "SESSION_PACKAGE_FAILED";
}

async function handleGetPending(sender: chrome.runtime.MessageSender, sendResponse: (r: unknown) => void): Promise<void> {
  const tabId = sender.tab?.id;
  if (tabId == null) {
    sendResponse({ ok: true, matched: false, launch: null });
    return;
  }
  let pending = await getPending(tabId);
  if (!pending) {
    const active = await getActive();
    const candidateUrls = [sender.url, sender.tab?.url].filter((u): u is string => Boolean(u));
    if (active && Date.now() <= active.expiresAt && candidateUrls.some((u) => urlsMatchForHandoff(active.applicationUrl, u))) {
      pending = { ...active, targetTabId: tabId };
      await putPending(tabId, pending);
    }
  }
  const pkg = pending ? await getPackage(tabId) : null;
  sendResponse({ ok: true, matched: Boolean(pending), launch: pending ? sanitize(pending) : null, session: pkg?.session ?? null });
}

// --------------------------------------------------------------------------- //
// Canonical autofill trigger (manual retry / continue after navigation)
// --------------------------------------------------------------------------- //
async function startAutofillForTab(
  tabId: number | undefined,
  reason: AutofillReason
): Promise<{ ok: boolean; error?: string }> {
  const id = await resolveViewTab(tabId);
  if (id == null) return { ok: false, error: "NO_TAB" };
  const pending = await getPending(id);
  if (!pending) return { ok: false, error: "SESSION_PACKAGE_FAILED" };
  const ready = await ensureContentReady(id);
  if (!ready) {
    await applyFailure(id, "CONTENT_SCRIPT_NOT_INJECTED");
    return { ok: false, error: "CONTENT_SCRIPT_NOT_INJECTED" };
  }
  try {
    await ensurePackage(id, pending);
  } catch {
    await applyFailure(id, "SESSION_PACKAGE_FAILED");
    return { ok: false, error: "SESSION_PACKAGE_FAILED" };
  }
  await patchView(id, { running: true, failureCode: null, failureMessage: null });
  await sendToTab(id, { type: MSG.AUTOFILL_START, reason });
  return { ok: true };
}

/** Ping the content script; if silent, inject it and ping again with backoff. */
async function ensureContentReady(tabId: number): Promise<boolean> {
  for (let attempt = 0; attempt < READY_MAX_ATTEMPTS; attempt += 1) {
    if (await pingContent(tabId)) return true;
    // Inject the compiled content script as a fallback (host permission
    // required — now static, so this always has access). allFrames so an
    // employer form embedded in an iframe (e.g. an ATS widget) is reachable
    // even when the declarative content_scripts registration missed it.
    try {
      await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["content.js"] });
    } catch (err) {
      log.warn("inject failed", { tabId, reason: String(err).slice(0, 40) });
    }
    if (await pingContent(tabId)) return true;
    await delay(Math.min(200 * 2 ** attempt, 2000));
  }
  return false;
}

function pingContent(tabId: number): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(tabId, { type: MSG.PING_CONTENT }, (resp) => {
        if (lastError()) return resolve(false);
        resolve(Boolean(resp && (resp as { ok?: boolean }).ok));
      });
    } catch {
      resolve(false);
    }
  });
}

// --------------------------------------------------------------------------- //
// Package: exchange the single-use token ONCE, then cache per tab
// --------------------------------------------------------------------------- //
async function ensurePackage(tabId: number, pending: PendingLaunch): Promise<SessionPackage> {
  const cached = await getPackage(tabId);
  if (cached) return cached;
  const inFlight = packageLoads.get(tabId);
  if (inFlight) return inFlight;
  const load = (async () => {
    const again = await getPackage(tabId);
    if (again) return again;
    const { session_token } = await exchangeLaunchToken(pending.launchToken);
    const session = await fetchSessionData(session_token, pending.sessionId);
    const pkg: SessionPackage = { sessionToken: session_token, session, cachedAt: Date.now() };
    await putPackage(tabId, pkg);
    await updatePending(tabId, { status: "detecting", state: "detecting_ats" });
    return pkg;
  })();
  packageLoads.set(tabId, load);
  try { return await load; } finally { packageLoads.delete(tabId); }
}

async function fetchDocument(
  tabId: number | undefined,
  sessionId: number,
  kind: "resume" | "cover-letter"
): Promise<{ dataUrl: string; filename: string }> {
  const id = await resolveViewTab(tabId);
  const pkg = id != null ? await getPackage(id) : null;
  if (!pkg) throw new Error("SESSION_PACKAGE_FAILED");
  const base = await getApiBase();
  const res = await fetch(`${base}/application-sessions/${sessionId}/${kind}?fmt=pdf`, {
    headers: { Authorization: `Bearer ${pkg.sessionToken}` }
  });
  if (!res.ok) throw new Error(`DOCUMENT_DOWNLOAD_FAILED_${res.status}`);
  const buffer = await res.arrayBuffer();
  if (buffer.byteLength === 0) throw new Error("DOCUMENT_DOWNLOAD_FAILED_EMPTY");
  const mime = res.headers.get("content-type")?.split(";")[0] || "application/pdf";
  const disposition = res.headers.get("content-disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const filename = encoded ? decodeURIComponent(encoded) : plain || `${kind}.pdf`;
  return { dataUrl: `data:${mime};base64,${toBase64(buffer)}`, filename };
}

async function auditEvent(
  tabId: number | undefined,
  sessionId: number,
  actionType: string,
  fieldKey?: string,
  status?: string
): Promise<void> {
  const id = await resolveViewTab(tabId);
  const pkg = id != null ? await getPackage(id) : null;
  if (!pkg) return;
  await postEvent(pkg.sessionToken, sessionId, { action_type: actionType, field_key: fieldKey, status });
}

/** "Save for future applications" from the review widget — the extension's
 * only answer-vault write, always an explicit user confirmation. Never
 * falsely reports success: SESSION_PACKAGE_FAILED / the API's own error surfaces
 * back to the widget so it can show a real failure instead of a false save. */
async function handleSaveAnswer(
  tabId: number | undefined,
  message: {
    sessionId: number;
    canonicalKey: string;
    value: string;
    displayValue?: string;
    scope?: string;
    companyKey?: string;
  }
): Promise<void> {
  const id = await resolveViewTab(tabId);
  const pkg = id != null ? await getPackage(id) : null;
  if (!pkg) throw new Error("SESSION_PACKAGE_FAILED");
  await saveSessionAnswer(pkg.sessionToken, message.sessionId, message.canonicalKey, {
    value: message.value,
    display_value: message.displayValue,
    scope: message.scope,
    company_key: message.companyKey
  });
}

async function handleConfirmName(
  tabId: number | undefined,
  message: {
    sessionId: number;
    firstName: string;
    lastName: string;
    middleName?: string;
    preferredFirstName?: string;
    preferredLastName?: string;
  }
): Promise<void> {
  const id = await resolveViewTab(tabId);
  const pkg = id != null ? await getPackage(id) : null;
  if (!pkg) throw new Error("SESSION_PACKAGE_FAILED");
  await confirmSessionName(pkg.sessionToken, message.sessionId, {
    firstName: message.firstName,
    lastName: message.lastName,
    middleName: message.middleName,
    preferredFirstName: message.preferredFirstName,
    preferredLastName: message.preferredLastName
  });
}

async function recordResult(
  tabId: number | undefined,
  sessionId: number,
  result: AutofillResult,
  progress: ProgressPayload
): Promise<void> {
  const id = await resolveViewTab(tabId);
  if (id != null) {
    await applyProgress(id, progress);
    await patchView(id, {
      running: false,
      state: result.status === "completed_with_review" ? "completed_with_review" : "completed"
    });
  }
  const pkg = id != null ? await getPackage(id) : null;
  if (pkg) await reportAutofillResult(pkg.sessionToken, sessionId, result).catch(() => undefined);
}

async function completeActive(sessionId: number): Promise<void> {
  // Find the package holding this session across tabs.
  const store = await chrome.storage.local.get("sessionPackages");
  const map = (store.sessionPackages as Record<string, SessionPackage>) || {};
  const entry = Object.values(map).find((p) => p.session.sessionId === sessionId);
  if (!entry) throw new Error("No active session token");
  await completeSession(entry.sessionToken, sessionId);
}

async function clearSession(tabId: number | undefined): Promise<void> {
  const id = await resolveViewTab(tabId);
  if (id == null) return;
  await sendToTab(id, { type: MSG.CLEAR_SESSION });
  await patchView(id, { running: false, filled: 0, skipped: 0, reviewRequired: 0 });
}

// --------------------------------------------------------------------------- //
// View-state updates
// --------------------------------------------------------------------------- //
async function applyProgress(tabId: number | undefined, p: ProgressPayload): Promise<void> {
  if (tabId == null) return;
  const durableStatus: PendingLaunch["status"] = p.state === "failed" ? "failed"
    : p.state === "completed_with_review" ? "review_required"
    : p.state === "completed" ? "ready"
    : p.state === "filling" ? "filling" : "detecting";
  await updatePending(tabId, { status: durableStatus, state: p.state });
  const resumeUploaded = p.documentsUploaded.includes("resume");
  const coverUploaded = p.documentsUploaded.includes("cover_letter");
  await patchView(tabId, {
    state: p.state,
    atsId: p.atsId,
    atsDisplayName: p.atsDisplayName,
    limited: p.limited,
    fieldsDiscovered: p.fieldsDiscovered,
    filled: p.filled,
    skipped: p.skipped,
    reviewRequired: p.reviewRequired,
    reachedFinalStep: p.reachedFinalStep,
    resumeStatus: resumeUploaded ? "uploaded" : p.reviewDocuments.includes("resume") ? "review" : "pending",
    coverStatus: coverUploaded ? "uploaded" : p.reviewDocuments.includes("cover_letter") ? "review" : "pending"
  });
  // Only the top frame owns the floating widget, but the authoritative fill may
  // run inside an embedded ATS iframe. Mirror the PII-free progress back to the
  // tab so the top-frame widget reflects the iframe's terminal state.
  await sendToTab(tabId, { type: MSG.AUTOFILL_PROGRESS, payload: p });
}

// Terminal handoff/session failures: retrying re-asks the background the same
// question and gets the same answer, so the widget/side panel must not offer
// a Retry that just repeats it — the user has to go back to JobPilot instead.
const TERMINAL_FAILURE_CODES = new Set([
  "HANDOFF_URL_MISMATCH", "WRONG_TAB", "HANDOFF_NOT_FOUND", "HANDOFF_EXPIRED",
  "HANDOFF_SCHEMA_OUTDATED", "TOKEN_CONSUMED", "SESSION_UNAUTHORIZED", "SESSION_NOT_FOUND"
]);

// --------------------------------------------------------------------------- //
// Frame registry
// --------------------------------------------------------------------------- //
/**
 * Sanitized per-frame probes, keyed by TRUSTED identity.
 *
 * The key is built from `sender.tab.id` and `sender.frameId`, which Chrome
 * supplies — never from anything in the message body, which a compromised page
 * could forge to impersonate another frame.
 *
 * Probes hold counts and scores only (see frames/probe.ts); no entered values,
 * tokens or full URLs ever reach this map.
 */
type RegisteredFrame = {
  tabId: number;
  frameId: number;
  isTopFrame: boolean;
  sanitizedUrl: string;
  rootConfident: boolean;
  applicationLabels: number;
  bestScore: number;
  at: number;
};

const frameRegistry = new Map<string, RegisteredFrame>();

const frameKey = (tabId: number, frameId: number): string => `${tabId}:${frameId}`;

export function registerFrameProbe(
  tabId: number,
  frameId: number,
  probe: { isTopFrame: boolean; sanitizedUrl: string; rootConfident: boolean; applicationLabelsFound: string[]; bestScore: number }
): void {
  frameRegistry.set(frameKey(tabId, frameId), {
    tabId,
    frameId,
    isTopFrame: probe.isTopFrame,
    sanitizedUrl: probe.sanitizedUrl,
    rootConfident: probe.rootConfident,
    applicationLabels: probe.applicationLabelsFound.length,
    bestScore: probe.bestScore,
    at: Date.now()
  });
}

/**
 * How long a frame probe may vouch for a page.
 *
 * Frame lifecycle is tracked by age rather than chrome.webNavigation: that API
 * would add a "read your browsing history" permission warning purely for a
 * bookkeeping nicety. A frame that still exists re-registers on every
 * CONTENT_READY, so a live application frame stays fresh; a frame that has
 * navigated away simply stops refreshing and expires.
 */
const FRAME_PROBE_TTL_MS = 60_000;

/** Frames in this tab (excluding `exceptFrameId`) that look like they hold a
 * real application. Used to veto a per-frame failure. */
function credibleApplicationFrames(tabId: number, exceptFrameId?: number): number[] {
  const out: number[] = [];
  const now = Date.now();
  for (const frame of frameRegistry.values()) {
    if (frame.tabId !== tabId) continue;
    if (exceptFrameId != null && frame.frameId === exceptFrameId) continue;
    if (now - frame.at > FRAME_PROBE_TTL_MS) continue;
    // Either a resolved root, or enough application labels that a rescan is
    // worthwhile (root scoring can fail mid-hydration).
    if (frame.rootConfident || frame.applicationLabels >= 2) out.push(frame.frameId);
  }
  return out;
}

/** Drop registrations for a tab (navigation, close, or session change) so a
 * stale frame can never vouch for a page that no longer exists. */
export function clearFrameRegistry(tabId: number, frameId?: number): void {
  for (const [key, frame] of frameRegistry) {
    if (frame.tabId !== tabId) continue;
    if (frameId != null && frame.frameId !== frameId) continue;
    frameRegistry.delete(key);
  }
}

/** Sanitized snapshot for Copy diagnostics. */
export function frameRegistrySnapshot(tabId: number): RegisteredFrame[] {
  return Array.from(frameRegistry.values()).filter((f) => f.tabId === tabId);
}

/**
 * Failure codes that describe ONE FRAME's view of the page, not the tab's.
 *
 * The live cross-origin failure: the application sits in an iframe, so the top
 * frame's own document genuinely has no application and reports
 * NO_APPLICATION_FORM. Because that was treated as a tab-level verdict, the
 * widget showed "JobPilot couldn't identify the application form on this page"
 * while the iframe held the entire application.
 *
 * These codes may only become a tab failure once NO frame in the tab has a
 * credible application.
 */
const PER_FRAME_FAILURE_CODES = new Set([
  "FORM_NOT_RENDERED",
  "NO_APPLICATION_FORM",
  "APPLICATION_FORM_AMBIGUOUS"
]);

async function applyFailure(
  tabId: number | undefined,
  code: string,
  message?: string,
  frameId?: number
): Promise<void> {
  if (tabId == null) return;
  // A form-hosting page can have several frames; a frame with no fields of its
  // own (an ad, a tracker, a sibling iframe, or a carrier page whose
  // application is embedded) legitimately fails while another frame holds the
  // real application. Never let that per-frame signal regress an in-progress or
  // completed tab, and never let it speak for frames it cannot see.
  if (PER_FRAME_FAILURE_CODES.has(code)) {
    const current = await getView(tabId);
    if (current && ["filling", "completed", "completed_with_review"].includes(current.state)) return;

    const others = credibleApplicationFrames(tabId, frameId);
    if (others.length > 0) {
      log.info("ignoring per-frame failure; another frame holds the application", {
        tabId, frameId: frameId ?? -1, code, otherFrames: others
      });
      return;
    }
  }
  const recoverable = !TERMINAL_FAILURE_CODES.has(code);
  await updatePending(tabId, {
    status: "failed", state: "failed", failureCode: code,
    lastError: { code, message: message ?? code, recoverable }
  });
  await patchView(tabId, {
    running: false, state: "failed", failureCode: code, failureMessage: message ?? null, failureRecoverable: recoverable
  });
}

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //
/**
 * Non-top frames are trusted once the TAB is bound to a handoff (their own
 * URL rarely resembles the employer page URL — e.g. a Greenhouse iframe
 * embedded in a MongoDB careers page). Only the top frame is re-checked
 * against the handoff URL on every call, so a tab that has since navigated
 * away from the employer page stops auto-filling.
 */
function validateLaunch(pending: PendingLaunch, url: string, tabId: number, isTopFrame: boolean): string | null {
  if (pending.protocolVersion !== PROTOCOL_VERSION) return "PROTOCOL_MISMATCH";
  if (pending.targetTabId != null && pending.targetTabId !== tabId) return "WRONG_TAB";
  if (Date.now() > pending.expiresAt) return "HANDOFF_EXPIRED";
  if (isTopFrame && !urlsMatchForHandoff(pending.applicationUrl, url)) return "HANDOFF_URL_MISMATCH";
  return null;
}

/** Never expose the launch token beyond the background. */
function sanitize(p: PendingLaunch): Omit<PendingLaunch, "launchToken"> {
  const { launchToken: _t, ...safe } = p;
  return safe;
}

async function resolveViewTab(tabId?: number): Promise<number | undefined> {
  if (tabId != null) return tabId;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id;
}

function sendToTab(tabId: number, message: object): Promise<void> {
  return new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(tabId, message, () => {
        void lastError();
        resolve();
      });
    } catch {
      resolve();
    }
  });
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function safeOrigin(url: string): string {
  try {
    return new URL(url).origin.toLowerCase();
  } catch {
    return "";
  }
}

function safeMessage(err: unknown): string {
  return err instanceof Error ? err.message.slice(0, 160) : "Unknown extension error";
}

function toBase64(buffer: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
