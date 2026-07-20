/**
 * Single source of truth for every message exchanged between the web page, the
 * JobPilot-origin content script, the employer-page content script, the
 * background service worker, and the side panel.
 *
 * There are two transports:
 *   • window.postMessage — page ⇆ JobPilot-origin content script (detection +
 *     staging the launch payload; a token is staged into the isolated content
 *     world and never left in the DOM).
 *   • chrome.runtime messaging — content/side panel ⇆ background.
 *
 * No raw message-type strings are duplicated elsewhere: everything imports the
 * `MSG` constants and the typed unions below, and `parseRuntimeMessage` /
 * `parsePageMessage` validate at runtime and reject unknown messages.
 */

import type { ApplicationSessionData } from "./types";

/** Bumped when the web⇆extension contract changes incompatibly. */
export const PROTOCOL_VERSION = 3;

// --------------------------------------------------------------------------- //
// Canonical launch state machine
// --------------------------------------------------------------------------- //
export type LaunchState =
  | "idle"
  | "preparing"
  | "package_ready"
  | "opening_tab"
  | "waiting_for_tab"
  | "waiting_for_content_script"
  | "fetching_package"
  | "detecting_ats"
  | "discovering_fields"
  | "filling"
  | "completed"
  | "completed_with_review"
  | "failed";

/** Why an autofill run was started — both paths call one canonical runner. */
export type AutofillReason = "automatic_launch" | "manual_retry" | "continue_after_navigation";

// --------------------------------------------------------------------------- //
// Field-level result model (Phase 12)
// --------------------------------------------------------------------------- //
export type FieldFillStatus =
  | "filled"
  | "already_filled"
  | "skipped"
  | "review"
  | "not_found"
  | "failed";

export type ReasonCode =
  | "NO_VERIFIED_ANSWER"
  | "LOW_CONFIDENCE"
  | "USER_VALUE_PRESENT"
  | "UNSUPPORTED_CONTROL"
  | "HIDDEN_FIELD"
  | "DISABLED_FIELD"
  | "SENSITIVE_FIELD"
  | "DOCUMENT_DOWNLOAD_FAILED"
  | "DOCUMENT_UPLOAD_REJECTED"
  | "VALUE_DID_NOT_STICK"
  | "ADAPTER_NOT_DETECTED"
  | "TOKEN_CONSUMED"
  // Diagnostics for the broad-domain handoff-gated injection model. Never carry
  // PII — these are structural/permission states only.
  | "HOST_PERMISSION_MISSING"
  | "CONTENT_SCRIPT_NOT_INJECTED"
  | "HANDOFF_URL_MISMATCH"
  | "NO_MATCHING_FRAME"
  | "FORM_NOT_RENDERED"
  | "NO_FIELDS_DISCOVERED"
  | "FILE_UPLOAD_FAILED"
  // Dropdown adapter outcomes (exact, per section N).
  | "DROPDOWN_NOT_VISIBLE"
  | "DROPDOWN_DISABLED"
  | "DROPDOWN_OPEN_FAILED"
  | "LISTBOX_NOT_FOUND"
  | "OPTIONS_NOT_FOUND"
  | "OPTION_NOT_AVAILABLE"
  | "DROPDOWN_SELECTION_FAILED"
  | "DROPDOWN_VERIFICATION_FAILED"
  // Handshake + handoff-lifecycle diagnostics (web bridge, stale storage).
  | "BRIDGE_NOT_READY"
  | "PROTOCOL_VERSION_MISMATCH"
  | "BACKGROUND_UNAVAILABLE"
  | "HANDOFF_NOT_FOUND"
  | "HANDOFF_EXPIRED"
  | "HANDOFF_SCHEMA_OUTDATED"
  | "SESSION_UNAUTHORIZED"
  | "SESSION_NOT_FOUND"
  | "SESSION_PACKAGE_FAILED";

export interface FieldFillResult {
  /** DiscoveredField.uid for this scan — lets the review widget look the live
   * element back up to apply a manual answer immediately. Absent for
   * synthetic entries (e.g. document uploads) that have no single field. */
  uid?: string;
  fieldKey: string;
  question: string;
  status: FieldFillStatus;
  reasonCode?: ReasonCode;
  confidence: number;
  /** Required vs optional, for the review widget's grouping/badges. */
  required?: boolean;
  /** Discovered option labels (select/radio/combobox) for the review widget
   * to offer as a picker instead of free text — never guessed, only what the
   * ATS page actually renders. */
  options?: string[];
  control?: string;
  /** Whether this canonical key is reusable across future applications (and
   * whether it defaults to "company" scope) — drives the widget's "Save for
   * future applications" option. Absent for unmapped/custom questions. */
  reusable?: boolean;
  defaultScope?: "global" | "company";
}

/** Safe, PII-free summary reported back to JobPilot. Counts + codes only. */
export type AutofillResult = {
  status: "completed" | "completed_with_review" | "partial" | "no_fields" | "failed" | "cancelled";
  ats: string | null;
  fields_discovered: number;
  fields_filled: number;
  documents_uploaded: ("resume" | "cover_letter")[];
  review_items: number;
  failures: { field_key: string; reason_code: string }[];
};

// --------------------------------------------------------------------------- //
// PendingLaunch — the prepared session bound to one exact employer tab
// --------------------------------------------------------------------------- //
export interface PendingLaunch {
  version: 1;
  applicationId: string;
  jobId: string;
  applicationUrl: string;
  status: "prepared" | "opening" | "detecting" | "filling" | "review_required" | "ready" | "failed";
  handoffToken: string;
  requestId: string;
  sessionId: number;
  launchToken: string;
  officialUrl: string;
  expectedOrigin: string;
  createdAt: number;
  expiresAt: number;
  targetTabId?: number;
  state: LaunchState;
  protocolVersion: number;
  atsType: string | null;
  /** Last actionable failure reason for the side panel (safe code, not PII). */
  failureCode?: ReasonCode | string;
  lastError?: { code: string; message: string; recoverable: boolean };
}

/** The launch payload the web app hands over (staged, then requested on click). */
export type LaunchPayload = {
  requestId: string;
  launchToken: string;
  sessionId: number;
  jobId: number;
  officialUrl: string;
  atsType: string | null;
};

/** State the side panel renders — persisted in chrome.storage.session per tab. */
export interface LaunchViewState {
  tabId: number;
  requestId: string;
  sessionId: number | null;
  state: LaunchState;
  company: string | null;
  jobTitle: string | null;
  atsId: string | null;
  atsDisplayName: string | null;
  limited: boolean;
  fieldsDiscovered: number;
  filled: number;
  skipped: number;
  reviewRequired: number;
  resumeStatus: "pending" | "uploaded" | "review" | "unavailable" | "—";
  coverStatus: "pending" | "uploaded" | "review" | "unavailable" | "—";
  reachedFinalStep: boolean;
  contentReady: boolean;
  packageLoaded: boolean;
  running: boolean;
  failureCode: string | null;
  failureMessage: string | null;
  /** false for a terminal failure (expired/consumed handoff, URL mismatch) —
   * the UI must not offer a Retry that will just repeat the same failure. */
  failureRecoverable: boolean | null;
  updatedAt: number;
}

// --------------------------------------------------------------------------- //
// Message-type constants (never inline a raw string anywhere else)
// --------------------------------------------------------------------------- //
export const MSG = {
  // page ⇆ JobPilot-origin content script (postMessage)
  PING: "JOBPILOT_PING",
  PONG: "JOBPILOT_PONG",
  STAGE_LAUNCH: "JOBPILOT_STAGE_LAUNCH",
  START_ASSISTED_APPLY: "JOBPILOT_START_ASSISTED_APPLY",
  START_ASSISTED_APPLY_RESULT: "JOBPILOT_START_ASSISTED_APPLY_RESULT",
  HANDSHAKE: "JOBPILOT_HANDSHAKE",
  // JobPilot-origin content script → background (runtime)
  LAUNCH_REQUEST: "JOBPILOT_LAUNCH_REQUEST",
  LAUNCH_ACCEPTED: "JOBPILOT_LAUNCH_ACCEPTED",
  LAUNCH_FAILED: "JOBPILOT_LAUNCH_FAILED",
  // employer content script ⇆ background (runtime)
  CONTENT_READY: "JOBPILOT_CONTENT_READY",
  GET_PENDING_LAUNCH: "JOBPILOT_GET_PENDING_LAUNCH",
  PING_CONTENT: "JOBPILOT_PING_CONTENT",
  PONG_CONTENT: "JOBPILOT_PONG_CONTENT",
  AUTOFILL_START: "JOBPILOT_AUTOFILL_START",
  AUTOFILL_PROGRESS: "JOBPILOT_AUTOFILL_PROGRESS",
  AUTOFILL_RESULT: "JOBPILOT_AUTOFILL_RESULT",
  AUTOFILL_FAILED: "JOBPILOT_AUTOFILL_FAILED",
  REQUEST_DOCUMENT: "JOBPILOT_REQUEST_DOCUMENT",
  AUDIT_EVENT: "JOBPILOT_AUDIT_EVENT",
  // side panel → background (runtime)
  START_AUTOFILL: "JOBPILOT_START_AUTOFILL",
  CLEAR_SESSION: "JOBPILOT_CLEAR_SESSION",
  COMPLETE_SESSION: "JOBPILOT_COMPLETE_SESSION",
  GET_VIEW_STATE: "JOBPILOT_GET_VIEW_STATE",
  // review widget → background → API (runtime)
  SAVE_ANSWER: "JOBPILOT_SAVE_ANSWER",
  CONFIRM_NAME: "JOBPILOT_CONFIRM_NAME",
} as const;

export type MsgType = (typeof MSG)[keyof typeof MSG];

// --------------------------------------------------------------------------- //
// Runtime (chrome.runtime) message union
// --------------------------------------------------------------------------- //
export type ProgressPayload = {
  tabId?: number;
  state: LaunchState;
  atsId: string | null;
  atsDisplayName: string | null;
  limited: boolean;
  fieldsDiscovered: number;
  filled: number;
  skipped: number;
  reviewRequired: number;
  reachedFinalStep: boolean;
  documentsUploaded: ("resume" | "cover_letter")[];
  reviewDocuments: ("resume" | "cover_letter")[];
};

export type RuntimeMessage =
  | { type: typeof MSG.LAUNCH_REQUEST; payload: LaunchPayload }
  | { type: typeof MSG.HANDSHAKE; origin: string; protocolVersion: number }
  | { type: typeof MSG.STAGE_LAUNCH; payload: LaunchPayload }
  | {
      type: typeof MSG.CONTENT_READY;
      url: string;
      title: string;
      protocolVersion: number;
      isTopFrame: boolean;
      topUrl: string | null;
      detectedAts: string | null;
      /** Sanitized per-frame application evidence (counts/scores only — see
       * frames/probe.ts). Lets the background rank frames against each other
       * instead of letting the top frame speak for the whole tab. */
      probe?: {
        isTopFrame: boolean;
        sanitizedUrl: string;
        rootConfident: boolean;
        applicationLabelsFound: string[];
        bestScore: number;
      };
    }
  | { type: typeof MSG.GET_PENDING_LAUNCH; url: string }
  | { type: typeof MSG.PING_CONTENT }
  | { type: typeof MSG.PONG_CONTENT; url: string }
  | { type: typeof MSG.AUTOFILL_START; reason: AutofillReason }
  | { type: typeof MSG.AUTOFILL_PROGRESS; payload: ProgressPayload }
  | { type: typeof MSG.AUTOFILL_RESULT; sessionId: number; result: AutofillResult; progress: ProgressPayload }
  | { type: typeof MSG.AUTOFILL_FAILED; reasonCode: ReasonCode | string; message?: string }
  | { type: typeof MSG.REQUEST_DOCUMENT; sessionId: number; kind: "resume" | "cover-letter" }
  | { type: typeof MSG.AUDIT_EVENT; sessionId: number; action_type: string; field_key?: string; status?: string }
  | { type: typeof MSG.START_AUTOFILL; tabId?: number; reason: AutofillReason }
  | { type: typeof MSG.CLEAR_SESSION; tabId?: number }
  | { type: typeof MSG.COMPLETE_SESSION; sessionId: number }
  | { type: typeof MSG.GET_VIEW_STATE; tabId?: number }
  | {
      type: typeof MSG.SAVE_ANSWER;
      sessionId: number;
      canonicalKey: string;
      value: string;
      displayValue?: string;
      scope?: "global" | "company" | "application" | "sensitive";
      companyKey?: string;
    }
  | {
      type: typeof MSG.CONFIRM_NAME;
      sessionId: number;
      firstName: string;
      lastName: string;
      middleName?: string;
      preferredFirstName?: string;
      preferredLastName?: string;
    };

const RUNTIME_TYPES = new Set<string>([
  MSG.LAUNCH_REQUEST, MSG.HANDSHAKE, MSG.STAGE_LAUNCH, MSG.CONTENT_READY, MSG.GET_PENDING_LAUNCH, MSG.PING_CONTENT, MSG.PONG_CONTENT,
  MSG.AUTOFILL_START, MSG.AUTOFILL_PROGRESS, MSG.AUTOFILL_RESULT, MSG.AUTOFILL_FAILED,
  MSG.REQUEST_DOCUMENT, MSG.AUDIT_EVENT, MSG.START_AUTOFILL, MSG.CLEAR_SESSION,
  MSG.COMPLETE_SESSION, MSG.GET_VIEW_STATE, MSG.SAVE_ANSWER, MSG.CONFIRM_NAME
]);

/** Validate an inbound runtime message; returns null for anything unknown. */
export function parseRuntimeMessage(raw: unknown): RuntimeMessage | null {
  if (!raw || typeof raw !== "object") return null;
  const type = (raw as { type?: unknown }).type;
  if (typeof type !== "string" || !RUNTIME_TYPES.has(type)) return null;
  return raw as RuntimeMessage;
}

// --------------------------------------------------------------------------- //
// Page (window.postMessage) message union
// --------------------------------------------------------------------------- //
export const PAGE_SOURCE_WEB = "jobpilot-web";
export const PAGE_SOURCE_EXT = "jobpilot-extension";

export type Capability = "fill" | "upload" | "results" | "ashby" | "greenhouse" | "lever" | "workday" | "generic";

export type ExtensionInfo = {
  installed: true;
  version: string;
  protocolVersion: number;
  capabilities: Capability[];
};

export type PageMessage =
  | { source: typeof PAGE_SOURCE_WEB; type: typeof MSG.PING }
  | { source: typeof PAGE_SOURCE_WEB; type: typeof MSG.STAGE_LAUNCH; payload: LaunchPayload }
  | { source: typeof PAGE_SOURCE_WEB; type: typeof MSG.START_ASSISTED_APPLY; payload: LaunchPayload }
  | { source: typeof PAGE_SOURCE_EXT; type: typeof MSG.START_ASSISTED_APPLY_RESULT; requestId: string; result: { ok: boolean; applicationId?: string; tabId?: number; code?: string; message?: string } }
  | { source: typeof PAGE_SOURCE_EXT; type: typeof MSG.PONG; info: ExtensionInfo };

export function parsePageMessage(raw: unknown): PageMessage | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as { source?: unknown; type?: unknown };
  if (data.source !== PAGE_SOURCE_WEB && data.source !== PAGE_SOURCE_EXT) return null;
  if (data.type !== MSG.PING && data.type !== MSG.STAGE_LAUNCH && data.type !== MSG.START_ASSISTED_APPLY && data.type !== MSG.START_ASSISTED_APPLY_RESULT && data.type !== MSG.PONG) return null;
  return raw as PageMessage;
}

// Re-exported so existing imports of the session type keep working.
export type { ApplicationSessionData };
