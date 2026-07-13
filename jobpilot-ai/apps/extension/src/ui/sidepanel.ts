/**
 * Side panel: the primary review UI. Shows detected ATS + progress, drives the
 * content script (fill / clear / rescan), and lets the user mark the application
 * complete after they submit it themselves. Never triggers submission.
 */

import type { ProgressState, RuntimeMessage } from "../messages";

let latest: ProgressState | null = null;

const el = (id: string) => document.getElementById(id) as HTMLElement;
const setText = (id: string, text: string) => {
  el(id).textContent = text;
};

async function activeTabId(): Promise<number | undefined> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id;
}

async function send(message: RuntimeMessage): Promise<void> {
  const tabId = await activeTabId();
  if (tabId != null) {
    try {
      await chrome.tabs.sendMessage(tabId, message);
    } catch {
      /* content script may not be present on this page */
    }
  }
}

function render(state: ProgressState): void {
  latest = state;
  setText("job", state.jobTitle ? `${state.jobTitle}${state.company ? " · " + state.company : ""}` : "Application detected");
  setText("ats", state.limited ? `${state.atsDisplayName} (limited)` : state.atsDisplayName);
  setText("filled", String(state.filled));
  setText("skipped", String(state.skipped));
  setText("review", String(state.reviewRequired));
  el("limited").hidden = !state.limited;
  el("final").hidden = !state.reachedFinalStep;

  const resume = state.session?.answers ? "Ready" : "—";
  setText("resume", resume);
  setText("cover", state.session ? "Ready" : "—");

  if (state.errors.length) {
    el("errors").hidden = false;
    el("errors").innerHTML = `<b>Issues</b><ul>${state.errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>`;
  } else {
    el("errors").hidden = true;
  }
}

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

chrome.runtime.onMessage.addListener((message: RuntimeMessage) => {
  if (message.type === "PROGRESS") {
    render(message.payload);
  }
});

el("fill").addEventListener("click", () => void send({ type: "FILL_APPLICATION" }));
el("rescan").addEventListener("click", () => void send({ type: "FILL_APPLICATION" }));
el("clear").addEventListener("click", () => void send({ type: "CLEAR_FIELDS" }));
el("next").addEventListener("click", () => void send({ type: "RESCAN" }));
el("complete").addEventListener("click", () => {
  if (!latest?.session) return;
  if (!confirm("Confirm you submitted this application on the employer's website?")) return;
  void chrome.runtime.sendMessage({ type: "COMPLETE_SESSION", sessionId: latest.session.sessionId } satisfies RuntimeMessage);
});

// Ask the active tab to report its current state on open.
void send({ type: "RESCAN" });
