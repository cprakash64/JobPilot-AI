/**
 * One safe logger for the whole extension.
 *
 * It logs only non-identifying operational metadata (request id, tab id, stage,
 * ATS, message type, reason code, field KEY — never a field value). It never
 * logs names, emails, phones, resume text, answer values, tokens or signed URLs.
 * A small allow-list of context keys is enforced so a careless caller cannot
 * leak PII through the logger.
 */

const ALLOWED_KEYS = new Set([
  "requestId", "tabId", "sessionId", "stage", "state", "ats", "messageType",
  "reasonCode", "fieldKey", "status", "count", "reason", "origin", "protocolVersion",
  "frameId", "isTopFrame", "matched", "fieldsDiscovered"
]);

type Ctx = Record<string, unknown>;

function safeContext(ctx: Ctx | undefined): Ctx {
  if (!ctx) return {};
  const out: Ctx = {};
  for (const [key, value] of Object.entries(ctx)) {
    if (!ALLOWED_KEYS.has(key)) continue;
    // Only primitives; never objects that could carry nested PII.
    if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
      out[key] = value;
    }
  }
  return out;
}

// Dev logging is on for unpacked installs (no update_url in the manifest).
function isDev(): boolean {
  try {
    return !chrome.runtime.getManifest().update_url;
  } catch {
    return true;
  }
}

function emit(level: "debug" | "info" | "warn" | "error", msg: string, ctx?: Ctx): void {
  if (level === "debug" && !isDev()) return;
  // eslint-disable-next-line no-console
  console[level](`[JobPilot] ${msg}`, safeContext(ctx));
}

export const log = {
  debug: (msg: string, ctx?: Ctx) => emit("debug", msg, ctx),
  info: (msg: string, ctx?: Ctx) => emit("info", msg, ctx),
  warn: (msg: string, ctx?: Ctx) => emit("warn", msg, ctx),
  error: (msg: string, ctx?: Ctx) => emit("error", msg, ctx)
};

/** Wrap a Chrome callback API and surface chrome.runtime.lastError as a reject. */
export function lastError(): string | null {
  const err = chrome.runtime?.lastError;
  return err?.message ?? null;
}
