/** Typed messages exchanged between the web page, content script, background,
 * and side panel. No employer page ever receives a token. */

import type { ApplicationSessionData } from "./types";

export type LaunchPayload = {
  launchToken: string;
  sessionId: number;
  officialUrl: string;
  atsType: string | null;
};

/** Safe, PII-free autofill summary reported back to JobPilot. Counts + codes
 * only — never field values or page HTML. */
export type AutofillResult = {
  status: "completed" | "completed_with_review" | "partial" | "no_fields" | "failed" | "cancelled";
  ats: string | null;
  fields_discovered: number;
  fields_filled: number;
  documents_uploaded: ("resume" | "cover_letter")[];
  review_items: number;
  failures: { field_key: string; reason_code: string }[];
};

export type RuntimeMessage =
  // content(JobPilot origin) -> background
  | { type: "LAUNCH_HANDOFF"; payload: LaunchPayload }
  // content(ATS page) -> background : exchange pending launch token for a session
  | { type: "REQUEST_SESSION"; url: string }
  // content(ATS page) -> background : fetch a generated document (base64) to upload
  | { type: "REQUEST_DOCUMENT"; sessionId: number; kind: "resume" | "cover-letter" }
  // content(ATS page) -> background : append an audit event
  | { type: "AUDIT_EVENT"; sessionId: number; action_type: string; field_key?: string; status?: string }
  // content(ATS page) -> background : record a safe autofill result summary
  | { type: "REPORT_RESULTS"; sessionId: number; result: AutofillResult }
  // sidepanel -> background : user confirms they submitted on the employer site
  | { type: "COMPLETE_SESSION"; sessionId: number }
  // sidepanel -> content : run actions on the active tab
  | { type: "FILL_APPLICATION" }
  | { type: "CLEAR_FIELDS" }
  | { type: "RESCAN" }
  // content -> sidepanel : progress updates
  | { type: "PROGRESS"; payload: ProgressState };

export type ProgressState = {
  company: string | null;
  jobTitle: string | null;
  atsId: string;
  atsDisplayName: string;
  limited: boolean;
  step: number;
  filled: number;
  skipped: number;
  reviewRequired: number;
  errors: string[];
  reachedFinalStep: boolean;
  session?: ApplicationSessionData;
};

export const PAGE_SOURCE_WEB = "jobpilot-web";
export const PAGE_SOURCE_EXT = "jobpilot-extension";

/** Bumped when the web<->extension message contract changes incompatibly. The
 * web app compares this to its own minimum and can ask the user to update. */
export const PROTOCOL_VERSION = 1;

/** What this extension build can do — surfaced to the web app in the handshake
 * so the UI can adapt without guessing. */
export type Capability = "fill" | "upload" | "results" | "ashby" | "greenhouse" | "lever" | "workday" | "generic";

/** Rich handshake reply so the web app knows the extension is present, its
 * version, the protocol it speaks, and what it supports. */
export type ExtensionInfo = {
  installed: true;
  version: string;
  protocolVersion: number;
  capabilities: Capability[];
};

export type PageMessage =
  | { source: typeof PAGE_SOURCE_WEB; type: "PING" }
  | { source: typeof PAGE_SOURCE_WEB; type: "LAUNCH"; payload: LaunchPayload }
  | { source: typeof PAGE_SOURCE_EXT; type: "PONG"; info: ExtensionInfo };
