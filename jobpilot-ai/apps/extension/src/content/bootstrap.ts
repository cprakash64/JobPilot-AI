/**
 * Content script with two roles depending on where it runs:
 *
 * 1. JobPilot web origin: a detection handshake (PING/PONG) and — critically —
 *    a CAPTURE-PHASE click listener on the JobPilot Apply button that forwards a
 *    LAUNCH_REQUEST to the background *synchronously within the real click*, so
 *    the background can open the side panel while the user gesture is still
 *    valid. The launch payload is staged into this isolated content world (never
 *    left in the DOM); only the request id lives on the button.
 *
 * 2. Employer/ATS page: announces readiness (CONTENT_READY), pulls the pending
 *    launch + session for THIS exact tab from the background, and runs the one
 *    canonical autofill runner automatically. It also answers a manual retry
 *    (AUTOFILL_START) with the same runner. It locates the submit control only
 *    to warn; it never clicks it.
 */

import { detectAdapter, type DetectionOutcome } from "../ats/registry";
import { clearJobPilotFields, fillField } from "../fields/fill";
import { EXTENSION_CAPABILITIES, isApprovedJobPilotOrigin } from "../config";
import { log } from "../logger";
import {
  MSG,
  PAGE_SOURCE_EXT,
  PROTOCOL_VERSION,
  parsePageMessage,
  parseRuntimeMessage,
  type AutofillReason,
  type ExtensionInfo,
  type LaunchPayload,
  type PageMessage,
  type ProgressPayload
} from "../messages";
import type { ApplicationSessionData, DiscoveredField } from "../types";
import {
  computeCounts,
  mergeLedger,
  type LedgerCounts,
  type LedgerEntry
} from "../fields/ledger";
import { runAutofill } from "./autofill";
import { buildDiagnostics } from "./diagnostics";
import { buildReviewModel } from "./review";
import { startTeachMode, type LearnScope, type LearnedAnswer } from "./teach";
import { resolveApplicationForm } from "../ats/base";
import type { FormRootResult } from "../ats/formRoot";
import { probeFrame } from "../frames/probe";
import { selectActivationControl } from "../ats/applicationSurface";
import { BUILD_INFO } from "../buildInfo";
import { createWidget, type ReviewHandlers, type ReviewItem } from "./widget";
import { claimContentInstance, makeContentInstanceId } from "./instance";
import { fillStructuredRepeaters } from "../fields/repeaters";

// Declarative injection and the background's readiness fallback can both run.
// The newest instance owns the frame; older current-build instances check this
// predicate and become inert. Crucially, we DO NOT skip when an old boolean
// guard is present: that is the frozen state left by an extension reload.
const isCurrentInstance = claimContentInstance(
  window as unknown as Record<string, unknown>,
  makeContentInstanceId(BUILD_INFO.buildId)
);

if (isApprovedJobPilotOrigin(location.origin)) {
  // Never let ATS-only code (DOM scanning, adapters) reach the web-origin role
  // even indirectly.
  try {
    initWebOrigin();
    log.debug("bridge loaded");
  } catch (err) {
    log.error("bridge init failed", { reason: String(err).slice(0, 60) });
  }
} else {
  void initAtsPage();
}

// --------------------------------------------------------------------------- //
// 1. JobPilot web origin: validated, acknowledged bridge
// --------------------------------------------------------------------------- //
function initWebOrigin(): void {
  const info: ExtensionInfo = {
    installed: true,
    version: chrome.runtime.getManifest?.().version ?? "0.0.0",
    protocolVersion: PROTOCOL_VERSION,
    capabilities: EXTENSION_CAPABILITIES
  };
  // Register the listener FIRST, synchronously, before anything async — the
  // web page may send WEB_PING immediately after its own readiness listener
  // goes up, and there must be no window where a ping could arrive unheard.
  window.addEventListener("message", async (event: MessageEvent) => {
    if (!isCurrentInstance()) return;
    if (event.source !== window) return;
    if (!isApprovedJobPilotOrigin(event.origin)) return;
    const data = parsePageMessage(event.data);
    if (!data) return;
    if (data.type === MSG.PING) {
      log.debug("web ping received");
      const ack = (await sendRuntime({ type: MSG.HANDSHAKE, origin: event.origin, protocolVersion: PROTOCOL_VERSION })) as
        | { ok?: boolean; protocolVersion?: number }
        | undefined;
      // PONG whenever the background is actually reachable — regardless of
      // whether ITS protocol version matches this (possibly stale, if the
      // extension was reloaded while this tab stayed open) content script's
      // compiled-in PROTOCOL_VERSION. The web app already compares
      // info.protocolVersion itself to decide "outdated" vs. current; what
      // must never happen is silently dropping the reply and collapsing
      // "installed but outdated/stale" into "not installed at all".
      // `ack === undefined` means chrome.runtime.sendMessage itself failed
      // (extension context invalidated — content script truly orphaned by a
      // reload) and there is genuinely no bridge to report; stay silent so
      // the web app's timeout-driven "reload the page" messaging is accurate.
      if (ack !== undefined) {
        window.postMessage({ source: PAGE_SOURCE_EXT, type: MSG.PONG, info } satisfies PageMessage, location.origin);
        log.debug("extension ready sent");
      } else {
        // Expected briefly during an unpacked-extension reload. The background
        // now revives this page automatically; logging at warn made Chrome list
        // a normal lifecycle transition as an extension error.
        log.debug("background temporarily unavailable; waiting for bridge revival");
      }
    } else if (data.type === MSG.STAGE_LAUNCH) {
      if (validLaunch(data.payload)) void sendRuntime({ type: MSG.STAGE_LAUNCH, payload: data.payload });
    } else if (data.type === MSG.START_ASSISTED_APPLY) {
      log.debug("start assisted apply forwarded");
      const result = (validLaunch(data.payload)
        ? await sendRuntime({ type: MSG.LAUNCH_REQUEST, payload: data.payload })
        : { ok: false, code: "INVALID_HANDOFF", message: "The prepared application handoff is invalid." }) as { ok: boolean; applicationId?: string; tabId?: number; code?: string; message?: string };
      log.debug("background acknowledgement received", { status: result.ok ? "ok" : "failed" });
      window.postMessage({ source: PAGE_SOURCE_EXT, type: MSG.START_ASSISTED_APPLY_RESULT, requestId: data.payload.requestId, result } satisfies PageMessage, location.origin);
    }
  });
}

function validLaunch(payload: LaunchPayload): boolean {
  if (!payload || !payload.requestId || !payload.launchToken || !Number.isInteger(payload.sessionId) || !Number.isInteger(payload.jobId)) return false;
  try { const u = new URL(payload.officialUrl); return u.protocol === "https:" || (u.protocol === "http:" && ["localhost", "127.0.0.1"].includes(u.hostname)); }
  catch { return false; }
}

// --------------------------------------------------------------------------- //
// 2. Employer/ATS page: readiness pull + canonical autofill
//
// host_permissions now cover every https(s) origin (employer/ATS forms live on
// domains we cannot enumerate up front), so this script is declaratively
// injected into every page. What keeps it dormant everywhere except a
// user-initiated JobPilot handoff is this gate: it NEVER scans the DOM,
// inserts the widget, or observes mutations until the background confirms
// this exact tab/frame matches an active, unexpired handoff. An unmatched
// frame registers only the (inert) message listener and exits immediately.
// --------------------------------------------------------------------------- //
const isTopFrame = window.top === window;

/** Sanitized evidence about THIS frame, sent with CONTENT_READY so the
 * background can rank frames. Best-effort: a probe failure must never block the
 * readiness handshake. */
function buildFrameProbe() {
  try {
    const p = probeFrame(document);
    return {
      isTopFrame: p.isTopFrame,
      sanitizedUrl: p.sanitizedUrl,
      rootConfident: p.rootConfident,
      applicationLabelsFound: p.applicationLabelsFound,
      bestScore: p.bestScore
    };
  } catch {
    return undefined;
  }
}
let session: ApplicationSessionData | null = null;
let outcome: DetectionOutcome | null = null;
let running = false;
let started = false;
let matched = false;
// Once a launch reaches a terminal ready/review/failure state, DOM changes
// (including the user's own answers) must not silently start autofill again.
// Only the explicit Retry action clears this latch.
let automaticRunSettled = false;
// Live element refs from the most recent scan, keyed by DiscoveredField.uid —
// how the review widget applies a manually-chosen answer to the right node.
let lastFields: Map<string, DiscoveredField> = new Map();
let widget: ReturnType<typeof createWidget> | null = null;
let lastScanSignature = "";
// The DURABLE field ledger, merged across every (re)scan. This — not any
// per-scan filter — is the single source of truth for counts and the review
// list, so a late partial SPA re-render can never drop an already-discovered
// required control and falsely report "All caught up".
let ledger: LedgerEntry[] = [];
let ledgerCounts: LedgerCounts | null = null;
/** The scored application-form root for this page (section A). */
let formRoot: FormRootResult | null = null;
let stopTeaching: (() => void) | null = null;

const FAILURE_MESSAGE: Record<string, string> = {
  HANDOFF_NOT_FOUND: "No prepared application is waiting for this tab. Start from JobPilot.",
  HANDOFF_URL_MISMATCH: "This page doesn't match the prepared application. Open it from JobPilot.",
  HANDOFF_EXPIRED: "This launch expired. Reopen the application from JobPilot.",
  HANDOFF_SCHEMA_OUTDATED: "The extension was updated. Reload this page to continue.",
  TOKEN_CONSUMED: "This launch was already used. Reopen the application from JobPilot.",
  SESSION_UNAUTHORIZED: "Your session is no longer valid. Reopen the application from JobPilot.",
  SESSION_NOT_FOUND: "This application session no longer exists. Reopen from JobPilot.",
  SESSION_PACKAGE_FAILED: "Your prepared application couldn't be loaded. Reopen from JobPilot."
};

// Mirrors background.ts's TERMINAL_FAILURE_CODES — retrying these re-asks the
// same question and gets the same answer, so the widget must not offer a
// Retry action that just repeats a permanently expired/invalid handoff.
const TERMINAL_FAILURE_CODES = new Set([
  "HANDOFF_URL_MISMATCH", "HANDOFF_NOT_FOUND", "HANDOFF_EXPIRED",
  "HANDOFF_SCHEMA_OUTDATED", "TOKEN_CONSUMED", "SESSION_UNAUTHORIZED", "SESSION_NOT_FOUND"
]);

async function initAtsPage(): Promise<void> {
  // Cheap and always safe to register: answers liveness pings and gives an
  // unmatched frame a second chance if the tab becomes bound to a handoff
  // shortly after this frame's own (negative) initial check — e.g. an
  // employer-embedded iframe whose content script races the top frame's.
  chrome.runtime.onMessage.addListener((raw, _sender, sendResponse) => {
    if (!isCurrentInstance()) return false;
    const message = parseRuntimeMessage(raw);
    if (!message) {
      sendResponse({ ok: false, error: "UNKNOWN_MESSAGE" });
      return false;
    }
    if (message.type === MSG.PING_CONTENT) {
      sendResponse({ ok: true, url: location.href });
      return false;
    }
    if (message.type === MSG.AUTOFILL_START) {
      if (message.reason === "manual_retry") automaticRunSettled = false;
      void (matched ? fill(message.reason) : checkHandoffAndStart(message.reason)).then(() => sendResponse({ ok: true }));
      return true;
    }
    if (message.type === MSG.AUTOFILL_PROGRESS) {
      // The application may live in a cross-origin iframe while only the top
      // frame owns the JobPilot widget. The background mirrors the iframe's
      // sanitized progress here so the top widget does not remain stuck on
      // "Opening the application…" after filling and uploads have completed.
      if (isTopFrame) mirrorFrameProgress(message.payload);
      sendResponse({ ok: true });
      return false;
    }
    if (message.type === MSG.CLEAR_SESSION) {
      if (matched) clearJobPilotFields(document);
      sendResponse({ ok: true });
      return false;
    }
    return false;
  });

  await checkHandoffAndStart("automatic_launch");
}

function mirrorFrameProgress(progress: ProgressPayload): void {
  if (!matched) return;
  const stage = progress.state === "completed"
    ? "ready"
    : progress.state === "completed_with_review"
      ? "review"
      : progress.state === "failed"
        ? "failed"
        : progress.state === "filling"
          ? "filling"
          : "detecting";
  const pending = progress.reviewRequired;
  widget = ensureWidget();
  widget.update({
    stage,
    filled: progress.filled,
    total: progress.fieldsDiscovered,
    message: stage === "ready"
      ? "Every required field is filled. Review everything before you submit."
      : stage === "review"
        ? `${pending} item${pending === 1 ? "" : "s"} need your review.`
        : stage === "failed"
          ? "Autofill failed. Retry or continue manually."
          : stage === "filling"
            ? "Filling verified fields…"
            : "Detecting fields…"
  });
}

/** Best-effort top-level URL; throws for a cross-origin iframe (expected —
 * the background already knows the top URL via chrome.tabs, it doesn't need
 * this frame to report it). */
function topFrameUrl(): string | null {
  if (isTopFrame) return location.href;
  try {
    return window.top?.location.href ?? null;
  } catch {
    return null;
  }
}

async function checkHandoffAndStart(reason: AutofillReason): Promise<void> {
  if (!isCurrentInstance()) return;
  const resp = (await sendRuntime({
    type: MSG.CONTENT_READY,
    probe: buildFrameProbe(),
    url: location.href,
    title: document.title,
    protocolVersion: PROTOCOL_VERSION,
    isTopFrame,
    topUrl: topFrameUrl(),
    detectedAts: null
  })) as { ok: boolean; matched?: boolean; error?: string; session?: ApplicationSessionData | null } | undefined;

  if (!resp?.matched) {
    // No matching handoff for this tab/frame — remain completely dormant.
    return;
  }
  matched = true;

  if (!resp.session) {
    // The tab IS bound to a handoff, but the session package could not be
    // loaded (expired/consumed token, backend error). Surface it — never
    // scan for fields with nothing to fill them with.
    if (isTopFrame) {
      widget = ensureWidget();
      widget.update({
        stage: "failed",
        message: FAILURE_MESSAGE[resp.error ?? ""] ?? `Could not load your prepared application (${resp.error ?? "unknown error"}).`,
        recoverable: !TERMINAL_FAILURE_CODES.has(resp.error ?? "")
      });
    }
    return;
  }

  // Matched AND session ready: this frame is authorized to scan and fill.
  session = resp.session;
  outcome = detectAdapter({ url: location.href, document });
  if (isTopFrame) {
    widget = ensureWidget();
    widget.update({ stage: "detecting", message: "Waiting for the application form…" });
  }
  started = true;
  void discoverAndFill(reason);
  observeMutations();
}

function ensureWidget(): ReturnType<typeof createWidget> {
  return (
    widget ??
    createWidget({
      retry: () => {
        automaticRunSettled = false;
        void (matched && session ? fill("manual_retry") : checkHandoffAndStart("manual_retry"));
      },
      clear: () => { if (matched) clearJobPilotFields(document); },
      complete: () => { if (session) void sendRuntime({ type: MSG.COMPLETE_SESSION, sessionId: session.sessionId }); },
      diagnostics: copyDiagnostics,
      teach: (enabled) => { if (enabled) beginTeaching(); else { stopTeaching?.(); stopTeaching = null; } }
    })
  );
}

async function discoverAndFill(reason: AutofillReason): Promise<void> {
  const deadline = Date.now() + 30_000;
  let attempt = 0;
  while (isCurrentInstance() && session && Date.now() < deadline) {
    outcome = detectAdapter({ url: location.href, document });
    const count = document.querySelectorAll("input:not([type=hidden]),textarea,select,[contenteditable=true]").length;
    log.debug("discovery attempt", { ats: outcome?.result.atsId ?? null, count, stage: attempt });
    if (outcome && count > 0) { await fill(reason); return; }
    widget?.update({ stage: "detecting", total: count, message: `Detecting fields (attempt ${attempt + 1})…` });
    await delay(Math.min(250 * 2 ** attempt, 2000));
    attempt += 1;
  }
  // A frame with no form of its own (a same-tab sibling iframe unrelated to
  // the application, or a top frame whose form actually lives in a nested
  // iframe) legitimately times out here. Only the top frame — which owns the
  // widget — reports this as a tab-level failure, and even then only after
  // confirming no other frame in the tab has already succeeded (the
  // background also refuses to regress an in-progress/completed tab).
  if (!isTopFrame) return;
  const view = (await sendRuntime({ type: MSG.GET_VIEW_STATE })) as { ok: boolean; view?: { state?: string } | null } | undefined;
  if (view?.view && ["filling", "completed", "completed_with_review"].includes(view.view.state ?? "")) return;
  widget?.update({ stage: "failed", message: "The application form did not render in time. You can retry." });
  void sendRuntime({ type: MSG.AUTOFILL_FAILED, reasonCode: "FORM_NOT_RENDERED", message: "No application form rendered within 30 seconds" });
}


// --------------------------------------------------------------------------- //
// Application-surface activation (section A)
// --------------------------------------------------------------------------- //
/** At most one activation click per run — never a retry loop of clicking. */
let activationDone = false;

/**
 * Reveal the application when the page hides it behind a tab/CTA.
 *
 * Only the top frame does this, and only once. The control is chosen by
 * ats/applicationSurface.ts, which excludes anything that could submit,
 * authenticate or transmit data. Revealing the form is inside what the user
 * asked for when they chose "Open and autofill application"; committing it is
 * not, and never happens here.
 */
async function activateApplicationSurfaceOnce(): Promise<void> {
  if (!isTopFrame || activationDone) return;

  const candidate = selectActivationControl(document);
  if (!candidate) {
    log.debug("no safe application-surface control on this page");
    // Nothing to reveal. The coordinator will time out and publish the failure.
    void sendRuntime({ type: MSG.AUTOFILL_FAILED, reasonCode: "FORM_NOT_RENDERED" });
    return;
  }

  activationDone = true;
  log.info(`activating application surface (${candidate.reason})`);
  widget?.update({ stage: "detecting", message: "Opening the application…" });

  try {
    candidate.element.scrollIntoView({ block: "center", behavior: "auto" });
    candidate.element.focus?.();
    candidate.element.click();
  } catch (err) {
    log.debug(`activation click failed: ${String(err).slice(0, 60)}`);
    return;
  }

  // The application may appear as a lazily-inserted iframe (Airbnb inserts
  // iframe#grnhse_iframe) or as newly-revealed in-document controls. Watch for
  // either, then rescan — the same run continues; this is not a new run.
  await waitForApplicationToAppear();
  await discoverAndFill("continue_after_navigation");
}

/** Bounded wait for a lazily-inserted application frame or new controls. */
function waitForApplicationToAppear(timeoutMs = 15000): Promise<void> {
  return new Promise((resolve) => {
    const started = Date.now();
    const done = () => {
      observer.disconnect();
      clearInterval(poll);
      resolve();
    };
    const appeared = () =>
      document.querySelector("iframe#grnhse_iframe,iframe[src*='greenhouse.io']") !== null ||
      resolveApplicationForm(document).confident;

    const observer = new MutationObserver(() => {
      if (appeared()) done();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });

    // A short poll as well: some frameworks swap nodes without a mutation the
    // observer is configured to see.
    const poll = setInterval(() => {
      if (appeared() || Date.now() - started > timeoutMs) done();
    }, 250);
  });
}

async function fill(reason: AutofillReason): Promise<void> {
  if (!isCurrentInstance() || !session || running) return;
  if (automaticRunSettled && reason !== "manual_retry") return;
  if (!outcome) outcome = detectAdapter({ url: location.href, document });
  if (!outcome) {
    void sendRuntime({ type: MSG.AUTOFILL_FAILED, reasonCode: "ADAPTER_NOT_DETECTED" });
    return;
  }
  running = true;
  started = true;

  // Scope EVERYTHING to the scored application-form root. Without a confident
  // root we refuse to scan: a global scan is what turned a site-search box into
  // an "application question".
  formRoot = resolveApplicationForm(document);
  if (!formRoot.confident) {
    running = false;
    const ambiguous = formRoot.reason === "APPLICATION_FORM_AMBIGUOUS";

    // An unresolved root is EXPECTED state while the application has not been
    // revealed yet (the live Airbnb page ships "Role overview" selected and no
    // Greenhouse iframe at all). Logging it at warn made Chrome's extension
    // error page show "[JobPilot] application root unresolved [object Object]"
    // during entirely normal probing.
    log.debug(`application root unresolved (${formRoot.reason ?? "unknown"}), ${formRoot.candidates.length} candidate(s)`);

    // Ambiguity is a real dead end — two different forms, nothing safe to pick.
    if (ambiguous) {
      widget?.update({
        stage: "failed",
        message:
          "JobPilot found more than one possible application form on this page and won't guess. Use Copy diagnostics to report it."
      });
      void sendRuntime({ type: MSG.AUTOFILL_FAILED, reasonCode: "APPLICATION_FORM_AMBIGUOUS" });
      return;
    }

    // Otherwise the application may simply not be on screen yet. Try to reveal
    // it, then let the run coordinator decide — a single frame may no longer
    // publish a terminal "no application" verdict for the whole tab.
    await activateApplicationSurfaceOnce();
    return;
  }

  // Employment and education are collections, not scalar answers. Expand and
  // fill those rows first so the generic scanner sees the final DOM shape and
  // cannot overwrite every repeated row with the current employer/school.
  const repeaterResults = await fillStructuredRepeaters(formRoot.root as ParentNode, session).catch((error) => {
    // A third-party form can replace a section while it is being expanded.
    // Keep the scalar fill usable and make the structured failure diagnosable
    // instead of leaving the content runner permanently stuck in `running`.
    log.warn("structured profile sections failed", {
      error: error instanceof Error ? error.name : "unknown"
    });
    return [];
  });
  if (repeaterResults.length) {
    log.info("structured profile sections processed", {
      sections: repeaterResults.length,
      requested: repeaterResults.reduce((sum, item) => sum + item.recordsRequested, 0),
      found: repeaterResults.reduce((sum, item) => sum + item.recordsFound, 0),
      filled: repeaterResults.reduce((sum, item) => sum + item.fieldsFilled, 0),
      failures: repeaterResults.reduce((sum, item) => sum + item.failures.length, 0)
    });
  }

  const total = (formRoot.root as ParentNode).querySelectorAll("input:not([type=hidden]),textarea,select,[contenteditable=true]").length;
  widget?.update({ stage: "filling", filled: 0, total, message: "Filling verified fields…" });
  try {
    const res = await runAutofill(session, outcome, {
      fetchDocument,
      onUploadStart: (kind) => widget?.update({ stage: "uploading", total, message: `Uploading ${kind === "resume" ? "resume" : "cover letter"}…` })
    }, currentStep());
    if (!isCurrentInstance()) return;
    lastScanSignature = scanSignature();
    // Merge (never replace) so a partial rescan can't drop earlier fields.
    for (const f of res.fields) lastFields.set(f.uid, f);
    ledger = mergeLedger(ledger, res.ledger);
    ledgerCounts = computeCounts(ledger);
    void sendRuntime({
      type: MSG.AUTOFILL_RESULT,
      sessionId: session.sessionId,
      result: res.result,
      progress: res.progress
    });
    if (await advanceWorkdayWhenSafe()) {
      // Workday replaces the page inside the same SPA. The mutation observer
      // will detect the next step and run the same verified fill pipeline.
      widget?.update({
        stage: "filling",
        filled: ledgerCounts.filled,
        total: ledgerCounts.discovered,
        message: "Current page complete. Moving to the next application step…"
      });
      return;
    } else if (ledgerCounts.discovered === 0) {
      automaticRunSettled = true;
      widget?.update({ stage: "failed", message: "No fillable fields were found on this page." });
      void sendRuntime({ type: MSG.AUTOFILL_FAILED, reasonCode: "NO_FIELDS_DISCOVERED" });
    } else {
      automaticRunSettled = true;
      const pending = ledgerCounts.pending;
      const atReview = Boolean(outcome.adapter.isReviewPage?.({ url: location.href, document }));
      assertReadyStateConsistent(ledgerCounts, pending === 0);
      widget?.update({
        stage: pending > 0 ? "review" : "ready",
        filled: ledgerCounts.filled,
        total: ledgerCounts.discovered,
        message: pending > 0
          ? `${atReview ? "At the final review: " : ""}${pending} item${pending === 1 ? "" : "s"} need your review${ledgerCounts.requiredBlank > 0 ? ` (${ledgerCounts.requiredBlank} required)` : ""}.`
          : atReview
            ? "Application complete. Nothing needs attention — review it and submit when ready."
            : "Every required field is filled. Review everything before you submit."
      });
    }
    if (isTopFrame) widget?.showReview(buildReviewItems(ledger, session), reviewHandlers, ledgerCounts);
    void sendRuntime({
      type: MSG.AUDIT_EVENT,
      sessionId: session.sessionId,
      action_type: "field_filled",
      status: `${res.result.fields_filled} filled`
    });
    log.info("autofill done", {
      ats: res.progress.atsId,
      reasonCode: reason,
      count: res.result.fields_filled
    });
  } catch (err) {
    automaticRunSettled = true;
    widget?.update({ stage: "failed", message: "Autofill failed. Retry or continue manually." });
    void sendRuntime({ type: MSG.AUTOFILL_FAILED, reasonCode: "VALUE_DID_NOT_STICK", message: String(err).slice(0, 80) });
  } finally {
    running = false;
  }
}

// A repeated DOM mutation must never click the same navigation control twice.
// The signature contains no entered values or other PII.
const workdayNavigationKeys = new Set<string>();

async function advanceWorkdayWhenSafe(): Promise<boolean> {
  if (!outcome || !ledgerCounts || outcome.result.atsId !== "workday") return false;
  const context = { url: location.href, document };
  if (outcome.adapter.isReviewPage?.(context)) return false;
  // No navigation while anything on this or an earlier page remains unresolved.
  if (ledgerCounts.pending > 0 || ledgerCounts.requiredBlank > 0 || ledgerCounts.technical > 0) return false;
  const next = outcome.adapter.findNextControl?.(context);
  if (!next) return false;
  const label = (next.textContent || (next as HTMLInputElement).value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const key = `${scanSignature()}|${label}`;
  if (workdayNavigationKeys.has(key)) return false;
  workdayNavigationKeys.add(key);
  lastScanSignature = scanSignature();
  next.scrollIntoView({ block: "center" });
  next.click();
  await delay(250);
  return true;
}

// --------------------------------------------------------------------------- //
// Review widget: unresolved-question list, live fill + save-for-future.
// --------------------------------------------------------------------------- //
/** Build the review list from the DURABLE ledger (pure logic lives in review.ts),
 * refreshing the uid -> key/scope maps the save flow depends on. */
function buildReviewItems(entries: LedgerEntry[], activeSession: ApplicationSessionData): ReviewItem[] {
  const model = buildReviewModel(entries, activeSession);
  lastCanonicalKeyByUid.clear();
  lastScopeByUid.clear();
  for (const [uid, key] of model.keyByUid) lastCanonicalKeyByUid.set(uid, key);
  for (const [uid, scope] of model.scopeByUid) lastScopeByUid.set(uid, scope);
  return model.items;
}

const reviewHandlers: ReviewHandlers = {
  async onFill(id, value) {
    if (id === "name_confirm") {
      // first|middle|last|preferredFirst|preferredLast
      const [given, middleRaw, family, prefFirstRaw, prefLastRaw] = (Array.isArray(value) ? value.join("|") : value).split("|");
      if (!given || !family || !session) return false;
      const middle = (middleRaw || "").trim();
      const resp = (await sendRuntime({
        type: MSG.CONFIRM_NAME,
        sessionId: session.sessionId,
        firstName: given,
        middleName: middle,
        lastName: family,
        preferredFirstName: (prefFirstRaw || "").trim(),
        preferredLastName: (prefLastRaw || "").trim()
      })) as { ok?: boolean } | undefined;
      if (!resp?.ok) return false;
      // A blank preferred field is treated as "no distinct preferred name" — the
      // legal name is used, per the common "enter your legal name" instruction.
      const prefFirst = (prefFirstRaw || "").trim() || given;
      const prefLast = (prefLastRaw || "").trim() || family;
      const valueForKey: Partial<Record<string, string>> = {
        first_name: given,
        last_name: family,
        // Only a TRUE full-name field gets every part — never a last-name field.
        full_name: [given, middle, family].filter(Boolean).join(" "),
        preferred_first_name: prefFirst,
        preferred_last_name: prefLast,
        preferred_name: prefFirst
      };
      if (middle) valueForKey.middle_name = middle;
      // Apply immediately to whichever discovered fields the field mapper already
      // classified — the same classification used for automatic fill, no separate
      // DOM-guessing heuristic here.
      const nameFills: Promise<unknown>[] = [];
      const resolved = new Set<string>();
      for (const [uid, canonicalKey] of lastCanonicalKeyByUid) {
        const field = lastFields.get(uid);
        const v = valueForKey[canonicalKey];
        if (!field || v === undefined) continue;
        nameFills.push(fillField(field, v, { status: "verified", force: true }));
        resolved.add(uid);
      }
      await Promise.all(nameFills);
      for (const uid of resolved) markLedgerResolved(uid, "user");
      refreshLedgerCounts();
      return true;
    }
    const field = lastFields.get(id);
    if (!field) return false;
    const canonicalKey = lastCanonicalKeyByUid.get(id);
    let valueToFill: string | string[] = value;
    let searchValue: string | undefined;
    if (canonicalKey === "city" && typeof value === "string") {
      const savedLocation = session?.profileData?.location;
      if (
        typeof savedLocation === "string"
        && savedLocation.includes(",")
        && savedLocation.toLowerCase().startsWith(`${value.trim().toLowerCase()},`)
      ) {
        valueToFill = savedLocation;
      }
      searchValue = value.trim();
    } else if (canonicalKey === "phone_country" && typeof value === "string") {
      searchValue = value.replace(/\s*\(\s*\+\d{1,4}\s*\)\s*$/, "").trim();
    }
    // The user's own choice goes through the SAME dropdown adapter as automatic
    // fill — open, select, and verify against the real DOM. There is no
    // simplified widget path, so "it worked in the widget" always means the
    // employer control actually changed. The widget steps aside first so its
    // Shadow-DOM overlay can never intercept an option click (section L).
    const outcome = await fillField(field, valueToFill, {
      status: "verified",
      force: true,
      answerSource: "user_confirmed_saved",
      dropdownSearchValue: searchValue,
      dropdownMatchMode: canonicalKey === "education_end_year"
        ? "graduation_year"
        : canonicalKey === "education_gpa"
          ? "gpa"
          : undefined,
      beforeInteract: () => widget?.setInteractionMode(true),
      afterInteract: () => widget?.setInteractionMode(false)
    });
    const ok = outcome.status === "filled";
    if (ok) {
      markLedgerResolved(id, "user");
      refreshLedgerCounts();
    }
    return ok;
  },
  async onSave(id, value, displayValue) {
    if (!session) return false;
    // The field's canonical key travels via the id -> fieldResults lookup at
    // render time isn't available here, so the widget always calls onFill
    // first; find the canonical key from the currently-known field mapping.
    const canonicalKey = lastCanonicalKeyByUid.get(id);
    if (!canonicalKey) return false;
    const scope = lastScopeByUid.get(id) ?? "global";
    const resp = (await sendRuntime({
      type: MSG.SAVE_ANSWER,
      sessionId: session.sessionId,
      canonicalKey,
      value,
      displayValue,
      scope
    })) as { ok?: boolean } | undefined;
    return Boolean(resp?.ok);
  },
  onJumpToField(id) {
    const field = lastFields.get(id);
    field?.element?.scrollIntoView({ behavior: "smooth", block: "center" });
    field?.element?.focus?.();
  }
};

// uid -> canonical key / default scope, refreshed each time review items are
// built, so onSave (which only receives the uid) knows what it's saving.
const lastCanonicalKeyByUid = new Map<string, string>();
const lastScopeByUid = new Map<string, "global" | "company">();

/** Mark a ledger entry resolved after the user answered it in the widget, so
 * counts + the "Mark complete" gate reflect the new state immediately without
 * waiting for the next rescan. */
function markLedgerResolved(uid: string, fillSource: string): void {
  const entry = ledger.find((e) => e.uid === uid);
  if (!entry) return;
  entry.status = "user_entered";
  entry.verified = true;
  entry.currentValuePresent = true;
  entry.fillSource = fillSource;
}

function refreshLedgerCounts(): void {
  ledgerCounts = computeCounts(ledger);
  widget?.refreshCounts(ledgerCounts);
}

/**
 * Turn on "Teach JobPilot": observe the user completing THIS application inside
 * the verified form root, and offer to remember each answer. Nothing is ever
 * persisted without the user picking a scope in the widget.
 */
function beginTeaching(): void {
  const root = formRoot?.root;
  if (!root || !widget) return;
  stopTeaching?.();
  stopTeaching = startTeachMode({
    root,
    fields: () => lastFields,
    ats: outcome?.result.atsId ?? null,
    employer: session?.company ?? null,
    canonicalKeyFor: (uid) => ledger.find((e) => e.uid === uid)?.canonicalKey ?? null,
    sensitiveFor: (uid) => Boolean(ledger.find((e) => e.uid === uid)?.sensitive),
    onRescanNeeded: () => { /* conditional fields are picked up by the observer */ },
    onLearned: (answer) => offerToRemember(answer)
  });
  log.info("teach mode enabled", { fields: lastFields.size });
}

function offerToRemember(answer: LearnedAnswer): void {
  widget?.askToRemember({
    id: answer.uid,
    question: answer.question || "This question",
    // A sensitive answer is never echoed back in the UI.
    chosenSummary: answer.sensitive ? "(your sensitive answer)" : answer.chosen.join(", "),
    employer: answer.employer,
    sensitive: answer.sensitive,
    proposedScope: answer.proposedScope,
    scopeConfident: answer.scopeConfident,
    onDecision: (scope) => void persistLearned(answer, scope)
  });
}

async function persistLearned(answer: LearnedAnswer, scope: LearnScope): Promise<void> {
  // "none" and "application" never leave the page: an application-only answer is
  // already typed into the form and has no reuse value.
  if (scope === "none" || scope === "application" || !session) return;
  await sendRuntime({
    type: MSG.SAVE_ANSWER,
    sessionId: session.sessionId,
    canonicalKey: answer.canonicalKey ?? answer.fingerprint,
    value: answer.chosen.join("|"),
    displayValue: answer.chosen.join(", "),
    scope: scope === "sensitive" ? "sensitive" : scope,
    companyKey: scope === "company" ? (answer.employer ?? "") : undefined
  });
  log.info("learned answer saved", { scope });
}

/** Development assertion (section C): a page with visible required blanks can
 * NEVER be reported ready. Logged loudly rather than thrown, so a bug surfaces
 * without breaking the user's application. */
function assertReadyStateConsistent(counts: LedgerCounts, readyForReview: boolean): void {
  if (counts.requiredBlank > 0 && readyForReview) {
    log.error("INVARIANT VIOLATED: ready reported with required blanks", {
      requiredBlank: counts.requiredBlank,
      discovered: counts.discovered
    });
  }
}

/** Copy sanitized diagnostics to the clipboard (dev aid). Structure + labels
 * only — NEVER entered values, name, email, phone, resume text, or tokens. */
function copyDiagnostics(): void {
  const labelSources: Record<string, string> = {};
  for (const [uid, field] of lastFields) labelSources[uid] = field.labelSource;
  const text = buildDiagnostics({
    url: location.href,
    atsId: outcome?.result.atsId ?? null,
    ledger,
    counts: ledgerCounts,
    formRoot,
    labelSources,
    extensionVersion: chrome.runtime.getManifest?.().version,
    // Re-probe at copy time so the census reflects the page as it is NOW,
    // not as it was when the content script first loaded.
    frameProbe: (() => { try { return probeFrame(document); } catch { return null; } })(),
    build: { version: BUILD_INFO.version, builtAt: BUILD_INFO.builtAt, buildId: BUILD_INFO.buildId }
  });
  try {
    void navigator.clipboard?.writeText(text);
  } catch {
    /* clipboard unavailable — the button is a best-effort dev aid */
  }
  log.info("diagnostics copied", { fields: ledger.length });
}

async function fetchDocument(kind: "resume" | "cover-letter"): Promise<File | null> {
  if (!session) return null;
  const resp = (await sendRuntime({ type: MSG.REQUEST_DOCUMENT, sessionId: session.sessionId, kind })) as
    | { ok: boolean; dataUrl?: string; filename?: string }
    | undefined;
  if (!resp?.ok || !resp.dataUrl) return null;
  const res = await fetch(resp.dataUrl);
  const blob = await res.blob();
  return new File([blob], resp.filename ?? `${kind}.pdf`, { type: blob.type || "application/pdf" });
}

// --------------------------------------------------------------------------- //
// Multi-step / SPA: re-scan on debounced DOM changes without duplicating fills.
// --------------------------------------------------------------------------- //
let debounce: ReturnType<typeof setTimeout> | null = null;
function observeMutations(): void {
  // A Workday application can legitimately take several minutes across its
  // account, profile, experience, disclosures, and review pages.
  const stopAt = Date.now() + 5 * 60_000;
  const observer = new MutationObserver(() => {
    if (!isCurrentInstance()) { observer.disconnect(); return; }
    if (Date.now() > stopAt) { observer.disconnect(); return; }
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => {
      // Continue filling any newly rendered fields. The fill engine skips fields
      // already filled or edited by the user, so this never duplicates values.
      if (session && !running && started && !automaticRunSettled && scanSignature() !== lastScanSignature) {
        void discoverAndFill("continue_after_navigation");
      }
      else emitProgressOnly();
    }, 500);
  });
  observer.observe(document.body, { childList: true, subtree: true });
  window.setTimeout(() => observer.disconnect(), 5 * 60_000);
}

function emitProgressOnly(): void {
  if (!isCurrentInstance() || !outcome) return;
  const submit = outcome.adapter.findSubmitControl({ url: location.href, document });
  const progress: ProgressPayload = {
    state: session ? "detecting_ats" : "waiting_for_content_script",
    atsId: outcome.result.atsId,
    atsDisplayName: outcome.adapter.displayName,
    limited: outcome.limited,
    fieldsDiscovered: 0,
    filled: 0,
    skipped: 0,
    reviewRequired: 0,
    reachedFinalStep: submit !== null,
    documentsUploaded: [],
    reviewDocuments: []
  };
  void sendRuntime({ type: MSG.AUTOFILL_PROGRESS, payload: progress });
}

function currentStep(): number {
  const text = document.body.textContent || "";
  const match = text.match(/step\s+(\d+)\s+of\s+\d+/i);
  return match ? Number(match[1]) : 0;
}

function sendRuntime(message: object): Promise<unknown> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(message, (resp) => {
        void chrome.runtime.lastError;
        resolve(resp);
      });
    } catch {
      resolve(undefined);
    }
  });
}

function delay(ms: number): Promise<void> { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
function scanSignature(): string {
  const controls = document.querySelectorAll("input:not([type=hidden]),textarea,select,[contenteditable=true]");
  const step = document.querySelector('[aria-current="step"], [data-automation-id*="progressBarActive" i]')?.textContent || "";
  const heading = Array.from(document.querySelectorAll("h1,h2,[role=heading]"))
    .map((item) => (item.textContent || "").trim())
    .filter(Boolean)
    .slice(0, 3)
    .join("|");
  const identities = Array.from(controls)
    .slice(0, 40)
    .map((item) => [item.getAttribute("name"), item.id, item.getAttribute("aria-label")].filter(Boolean).join(":"))
    .join("|");
  return `${location.href.split("#")[0]}|${controls.length}|${step.trim()}|${heading}|${identities}`;
}
