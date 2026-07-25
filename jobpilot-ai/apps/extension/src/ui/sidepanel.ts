/**
 * Side panel: an OBSERVER of the tab-scoped launch state stored in
 * chrome.storage.local. It never drives detection itself and never blocks
 * autofill on its own rendering — autofill starts automatically from the launch.
 *
 * It subscribes to storage changes for the current employer tab and renders the
 * stage, ATS, counters, document status, review items and any actionable failure
 * reason. "Fill application" triggers the SAME canonical runner as the automatic
 * launch (START_AUTOFILL → background → content), and is disabled while a run is
 * active. Every button surfaces chrome.runtime.lastError instead of failing
 * silently. It never submits.
 */

import { lastError } from "../logger";
import { MSG, PROTOCOL_VERSION, type LaunchViewState } from "../messages";
import { STORAGE_KEYS } from "../state";

const el = (id: string) => document.getElementById(id) as HTMLElement;
const setText = (id: string, text: string) => {
  el(id).textContent = text;
};

let tabId: number | undefined;
let view: LaunchViewState | null = null;

const STAGE_LABEL: Record<string, string> = {
  idle: "Idle",
  preparing: "Preparing…",
  package_ready: "Ready",
  opening_tab: "Opening application…",
  waiting_for_tab: "Opening application…",
  waiting_for_content_script: "Loading the application form…",
  fetching_package: "Loading your prepared application…",
  detecting_ats: "Detecting application…",
  discovering_fields: "Reading the form…",
  filling: "Filling your application…",
  completed: "Filled — review and submit",
  completed_with_review: "Filled — some items need your review",
  failed: "Something needs your attention"
};

const FAILURE_LABEL: Record<string, string> = {
  CONTENT_SCRIPT_NOT_INJECTED: "Couldn’t reach the application page. Reload it and click “Fill application”.",
  SESSION_PACKAGE_FAILED: "Your prepared application couldn’t be loaded. Reopen from JobPilot.",
  SESSION_UNAUTHORIZED: "Your session is no longer valid. Reopen the application from JobPilot.",
  SESSION_NOT_FOUND: "This application session no longer exists. Reopen from JobPilot.",
  TOKEN_CONSUMED: "This launch was already used. Reopen the application from JobPilot.",
  HANDOFF_EXPIRED: "This launch expired. Reopen the application from JobPilot.",
  HANDOFF_SCHEMA_OUTDATED: "The extension was updated. Reload this page to continue.",
  ADAPTER_NOT_DETECTED: "This application form isn’t supported yet. Fill it manually.",
  WRONG_ORIGIN: "The opened page didn’t match the expected employer. Nothing was filled.",
  WRONG_TAB: "This panel is bound to a different tab.",
  DOCUMENT_UPLOAD_REJECTED: "The employer blocked automatic file upload. Attach the document manually.",
  HOST_PERMISSION_MISSING: "JobPilot needs permission to access this site. Check the extension's site access settings.",
  HANDOFF_NOT_FOUND: "No prepared application is waiting for this tab. Start from JobPilot.",
  HANDOFF_URL_MISMATCH: "This page doesn’t match the prepared application. Open it from JobPilot.",
  FORM_NOT_RENDERED: "The application form did not render in time. You can retry.",
  NO_FIELDS_DISCOVERED: "No fillable fields were found on this page."
};

async function currentTabId(): Promise<number | undefined> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id;
}

function render(): void {
  const v = view;
  if (!v) {
    setText("job", "Waiting for an application…");
    setText("stage", "Open an application from JobPilot to begin.");
    return;
  }
  setText("job", v.jobTitle ? `${v.jobTitle}${v.company ? " · " + v.company : ""}` : "Application detected");
  setText("stage", STAGE_LABEL[v.state] ?? v.state);
  setText("ats", v.atsDisplayName ? (v.limited ? `${v.atsDisplayName} (limited)` : v.atsDisplayName) : "Detecting…");
  setText("discovered", String(v.fieldsDiscovered));
  setText("filled", String(v.filled));
  setText("skipped", String(v.skipped));
  setText("review", String(v.reviewRequired));
  el("limited").hidden = !v.limited;
  el("final").hidden = !v.reachedFinalStep;
  setText("resume", docLabel(v.resumeStatus));
  setText("cover", docLabel(v.coverStatus));

  const errBox = el("errors");
  if (v.failureCode) {
    errBox.hidden = false;
    errBox.textContent = FAILURE_LABEL[v.failureCode] ?? v.failureMessage ?? `Issue: ${v.failureCode}`;
  } else {
    errBox.hidden = true;
  }

  // A terminal failure (expired/consumed/mismatched handoff) can't be fixed
  // by retrying — the user has to reopen the application from JobPilot.
  const terminal = v.failureCode != null && v.failureRecoverable === false;
  (el("fill") as HTMLButtonElement).disabled = v.running || terminal;
  setText("fill", v.running ? "Filling…" : terminal ? "Reopen from JobPilot" : "Fill application");

  renderDiagnostics(v);
}

function docLabel(status: string): string {
  switch (status) {
    case "uploaded": return "Uploaded ✓";
    case "review": return "Attach manually";
    case "pending": return "Preparing…";
    case "unavailable": return "Unavailable";
    default: return "—";
  }
}

function renderDiagnostics(v: LaunchViewState): void {
  const isDev = !chrome.runtime.getManifest().update_url;
  const diag = el("diag");
  diag.hidden = !isDev;
  if (!isDev) return;
  el("diagBody").textContent = [
    `version: ${chrome.runtime.getManifest().version}`,
    `protocol: ${PROTOCOL_VERSION}`,
    `tabId: ${v.tabId}`,
    `state: ${v.state}`,
    `contentReady: ${v.contentReady}`,
    `packageLoaded: ${v.packageLoaded}`,
    `adapter: ${v.atsId ?? "—"}`,
    `lastFailure: ${v.failureCode ?? "—"}`
  ].join("\n");
}

async function refresh(): Promise<void> {
  tabId = await currentTabId();
  if (tabId == null) return;
  const store = await chrome.storage.local.get(STORAGE_KEYS.VIEW_KEY);
  const map = (store[STORAGE_KEYS.VIEW_KEY] as Record<string, LaunchViewState>) || {};
  view = map[String(tabId)] ?? null;
  render();
}

// React live to any change in the stored view state for our tab.
chrome.storage.local.onChanged.addListener((changes) => {
  const change = changes[STORAGE_KEYS.VIEW_KEY];
  if (!change || tabId == null) return;
  const map = (change.newValue as Record<string, LaunchViewState>) || {};
  view = map[String(tabId)] ?? view;
  render();
});

// Re-bind if the user switches which tab is active.
chrome.tabs.onActivated.addListener(() => void refresh());

function sendBackground(message: object): Promise<{ ok?: boolean; error?: string } | undefined> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(message, (resp) => {
        const err = lastError();
        if (err) return resolve({ ok: false, error: err });
        resolve(resp as { ok?: boolean; error?: string });
      });
    } catch (e) {
      resolve({ ok: false, error: String(e) });
    }
  });
}

function showButtonError(message: string): void {
  const errBox = el("errors");
  errBox.hidden = false;
  errBox.textContent = message;
}

el("fill").addEventListener("click", async () => {
  const resp = await sendBackground({ type: MSG.START_AUTOFILL, tabId, reason: "manual_retry" });
  if (resp && resp.ok === false) {
    showButtonError(FAILURE_LABEL[resp.error ?? ""] ?? `Couldn’t start: ${resp.error ?? "unknown error"}`);
  }
});
el("rescan").addEventListener("click", async () => {
  const resp = await sendBackground({ type: MSG.START_AUTOFILL, tabId, reason: "continue_after_navigation" });
  if (resp && resp.ok === false) showButtonError(`Couldn’t continue: ${resp.error ?? "unknown error"}`);
});
el("next").addEventListener("click", () => void refresh());
el("clear").addEventListener("click", () => void sendBackground({ type: MSG.CLEAR_SESSION, tabId }));
el("complete").addEventListener("click", async () => {
  if (!view?.sessionId) return;
  if (!confirm("Confirm you submitted this application on the employer's website?")) return;
  const resp = await sendBackground({ type: MSG.COMPLETE_SESSION, sessionId: view.sessionId });
  if (resp && resp.ok === false) showButtonError(`Couldn’t mark complete: ${resp.error ?? "unknown error"}`);
});

void refresh();
