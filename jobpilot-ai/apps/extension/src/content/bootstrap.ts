/**
 * Content script. Two roles depending on where it runs:
 *
 * 1. On the JobPilot web origin: a message handshake so the web app can detect
 *    the extension (PING/PONG) and hand off the one-time launch token (LAUNCH).
 * 2. On an employer/ATS page: detect the ATS, obtain the session from the
 *    background (which holds the tokens), and — only on the user's command from
 *    the side panel — fill standard fields and attach documents. It locates the
 *    final submit control solely to WARN the user; it never clicks it.
 */

import { detectAdapter, type DetectionOutcome } from "../ats/registry";
import { pickApplicationForm } from "../ats/base";
import { clearJobPilotFields } from "../fields/fill";
import { applyFill, scan } from "../fields/runner";
import { uploadFileToInput } from "../fields/upload";
import { EXTENSION_CAPABILITIES, JOBPILOT_WEB_ORIGINS } from "../config";
import {
  PAGE_SOURCE_EXT,
  PAGE_SOURCE_WEB,
  PROTOCOL_VERSION,
  type AutofillResult,
  type ExtensionInfo,
  type PageMessage,
  type ProgressState,
  type RuntimeMessage
} from "../messages";
import type { ApplicationSessionData } from "../types";

if (JOBPILOT_WEB_ORIGINS.includes(location.origin)) {
  initWebHandshake();
} else {
  void initAtsPage();
}

// --------------------------------------------------------------------------- //
// 1. JobPilot web origin: handshake + launch handoff
// --------------------------------------------------------------------------- //
function initWebHandshake(): void {
  const info: ExtensionInfo = {
    installed: true,
    version: chrome.runtime.getManifest?.().version ?? "0.0.0",
    protocolVersion: PROTOCOL_VERSION,
    capabilities: EXTENSION_CAPABILITIES
  };
  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== window) return;
    const data = event.data as PageMessage | undefined;
    if (data?.source !== PAGE_SOURCE_WEB) return;
    if (data.type === "PING") {
      // Reply with version + capabilities so the web app doesn't merely guess.
      window.postMessage({ source: PAGE_SOURCE_EXT, type: "PONG", info } satisfies PageMessage, location.origin);
    } else if (data.type === "LAUNCH") {
      void chrome.runtime.sendMessage({ type: "LAUNCH_HANDOFF", payload: data.payload } satisfies RuntimeMessage);
    }
  });
}

// --------------------------------------------------------------------------- //
// 2. Employer/ATS page: detect, obtain session, fill on command
// --------------------------------------------------------------------------- //
let session: ApplicationSessionData | null = null;
let outcome: DetectionOutcome | null = null;

async function initAtsPage(): Promise<void> {
  outcome = detectAdapter({ url: location.href, document });
  if (!outcome) return;

  const resp = (await chrome.runtime.sendMessage({ type: "REQUEST_SESSION", url: location.href } satisfies RuntimeMessage)) as
    | { ok: boolean; data: ApplicationSessionData | null }
    | undefined;
  session = resp?.ok ? resp.data : null;

  chrome.runtime.onMessage.addListener((message: RuntimeMessage) => {
    if (message.type === "FILL_APPLICATION") void fillNow();
    else if (message.type === "CLEAR_FIELDS") clearNow();
    else if (message.type === "RESCAN") report();
  });

  observeMutations();
  report();
}

async function fillNow(): Promise<void> {
  if (!session || !outcome) return;
  const root = pickApplicationForm(document);
  const scanned = scan(root, session, currentStep());
  const summary = applyFill(scanned.fields, scanned.mappings, session);

  const uploaded: ("resume" | "cover_letter")[] = [];
  const uploadFailures: { field_key: string; reason_code: string }[] = [];
  for (const target of summary.uploadTargets) {
    const field = scanned.fields.find((f) => f.uid === target.uid);
    if (field?.element) {
      const ok = await uploadDocument(field.element as HTMLInputElement, target.kind);
      if (ok) uploaded.push(target.kind === "cover-letter" ? "cover_letter" : "resume");
      else uploadFailures.push({ field_key: target.kind, reason_code: "UPLOAD_FAILED" });
    }
  }
  void chrome.runtime.sendMessage({
    type: "AUDIT_EVENT", sessionId: session.sessionId, action_type: "field_filled",
    status: `${summary.filled} filled`
  } satisfies RuntimeMessage);

  reportResults(scanned.fields.length, summary, uploaded, uploadFailures);
  report(summary.filled, summary.skipped, summary.reviewRequired, summary.errors);
}

/** Send a safe, PII-free result summary to the backend (via background). */
function reportResults(
  discovered: number,
  summary: { filled: number; reviewRequired: number; errors: string[] },
  uploaded: ("resume" | "cover_letter")[],
  uploadFailures: { field_key: string; reason_code: string }[]
): void {
  if (!session || !outcome) return;
  const status: AutofillResult["status"] =
    discovered === 0 ? "no_fields" : summary.reviewRequired > 0 ? "completed_with_review" : "completed";
  const result: AutofillResult = {
    status,
    ats: outcome.result.atsId,
    fields_discovered: discovered,
    fields_filled: summary.filled,
    documents_uploaded: uploaded,
    review_items: summary.reviewRequired,
    // Only generic codes — never field values or page text.
    failures: uploadFailures
  };
  void chrome.runtime.sendMessage({
    type: "REPORT_RESULTS", sessionId: session.sessionId, result
  } satisfies RuntimeMessage);
}

function clearNow(): void {
  clearJobPilotFields(document);
  report(0, 0, 0, []);
}

async function uploadDocument(input: HTMLInputElement, kind: "resume" | "cover-letter"): Promise<boolean> {
  if (!session) return false;
  const resp = (await chrome.runtime.sendMessage({
    type: "REQUEST_DOCUMENT", sessionId: session.sessionId, kind
  } satisfies RuntimeMessage)) as { ok: boolean; dataUrl?: string; filename?: string } | undefined;
  if (!resp?.ok || !resp.dataUrl) return false;
  const file = await dataUrlToFile(resp.dataUrl, resp.filename ?? `${kind}.pdf`);
  const result = uploadFileToInput(input, file);
  if (result.status === "uploaded") {
    void chrome.runtime.sendMessage({
      type: "AUDIT_EVENT", sessionId: session.sessionId, action_type: "document_uploaded", field_key: kind
    } satisfies RuntimeMessage);
    return true;
  }
  return false;
}

function report(filled = 0, skipped = 0, reviewRequired = 0, errors: string[] = []): void {
  if (!outcome) return;
  const submit = outcome.adapter.findSubmitControl({ url: location.href, document });
  const progress: ProgressState = {
    company: session?.company ?? null,
    jobTitle: session?.jobTitle ?? null,
    atsId: outcome.result.atsId,
    atsDisplayName: outcome.adapter.displayName,
    limited: outcome.limited,
    step: currentStep(),
    filled,
    skipped,
    reviewRequired,
    errors,
    reachedFinalStep: submit !== null,
    session: session ?? undefined
  };
  void chrome.runtime.sendMessage({ type: "PROGRESS", payload: progress } satisfies RuntimeMessage);
}

// --------------------------------------------------------------------------- //
// Multi-step: re-scan on debounced DOM changes without looping.
// --------------------------------------------------------------------------- //
let debounce: ReturnType<typeof setTimeout> | null = null;
function observeMutations(): void {
  const observer = new MutationObserver(() => {
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => report(), 500);
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

function currentStep(): number {
  const text = document.body.textContent || "";
  const match = text.match(/step\s+(\d+)\s+of\s+\d+/i);
  return match ? Number(match[1]) : 0;
}

async function dataUrlToFile(dataUrl: string, filename: string): Promise<File> {
  const res = await fetch(dataUrl);
  const blob = await res.blob();
  return new File([blob], filename, { type: blob.type || "application/pdf" });
}
