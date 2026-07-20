/** Extension configuration. The API base can be overridden via extension
 * storage for staging/production without rebuilding. */
export const DEFAULT_API_BASE = "http://localhost:8000";

export async function getApiBase(): Promise<string> {
  try {
    const stored = await chrome.storage.local.get("apiBase");
    return typeof stored.apiBase === "string" && stored.apiBase ? stored.apiBase : DEFAULT_API_BASE;
  } catch {
    return DEFAULT_API_BASE;
  }
}

/** Origins allowed to hand a launch token to the extension. Kept in sync with
 * the manifest `content_scripts`, `host_permissions`, and `externally_connectable`
 * so the JobPilot-origin role only activates on trusted JobPilot origins. */
export const JOBPILOT_WEB_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000", "https://app.jobpilot.ai"];
export function isApprovedJobPilotOrigin(origin: string): boolean {
  return JOBPILOT_WEB_ORIGINS.includes(origin);
}

import type { Capability } from "./messages";

/** What this build advertises in the detection handshake. */
export const EXTENSION_CAPABILITIES: Capability[] = [
  "fill",
  "upload",
  "results",
  "ashby",
  "greenhouse",
  "lever",
  "workday",
  "generic"
];
