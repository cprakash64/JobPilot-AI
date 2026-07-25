/** Secure API client. Uses a session-scoped token (never the user's main login
 * token) and only ever touches endpoints for the one application session. */

import { getApiBase } from "../config";
import type { AutofillResult } from "../messages";
import type { ApplicationSessionData, SessionAnswer } from "../types";

/** Carries the HTTP status so callers (background.ts) can classify a failure
 * precisely — 401 vs. 404 vs. 410 vs. anything else — instead of guessing
 * from a string. Never carries the response body (may contain PII). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, path: string) {
    super(`API ${status} for ${path}`);
    this.status = status;
  }
}

async function request<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const base = await getApiBase();
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...init.headers }
  });
  if (!res.ok) {
    throw new ApiError(res.status, path);
  }
  return (await res.json()) as T;
}

export async function exchangeLaunchToken(
  launchToken: string
): Promise<{ session_token: string; session: RawSession }> {
  const base = await getApiBase();
  const res = await fetch(`${base}/application-sessions/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ launch_token: launchToken })
  });
  if (!res.ok) {
    throw new ApiError(res.status, "/application-sessions/token");
  }
  return (await res.json()) as { session_token: string; session: RawSession };
}

type RawSession = {
  session_id: number;
  ats_type: string | null;
  official_application_url: string;
  job?: { title: string | null; company: string | null };
  resume?: { status: string; document_id: number | null; download_url: string | null };
  cover_letter?: { status: string; document_id: number | null; download_url: string | null };
  profile?: Record<string, unknown>;
};

export async function fetchSessionData(token: string, sessionId: number): Promise<ApplicationSessionData> {
  const session = await request<RawSession>(`/application-sessions/${sessionId}`, token);
  const answers = await request<{ answers: SessionAnswer[]; unresolved_questions: { canonical_key: string; reason?: string }[] }>(
    `/application-sessions/${sessionId}/answers`,
    token
  );
  return {
    sessionId: session.session_id,
    atsType: session.ats_type,
    officialUrl: session.official_application_url,
    jobTitle: session.job?.title ?? null,
    company: session.job?.company ?? null,
    profileData: session.profile ?? {},
    answers: answers.answers,
    unresolvedQuestions: answers.unresolved_questions,
    documents: {
      resume: { status: session.resume?.status ?? "missing", documentId: session.resume?.document_id ?? null, downloadPath: session.resume?.download_url ?? null },
      coverLetter: { status: session.cover_letter?.status ?? "missing", documentId: session.cover_letter?.document_id ?? null, downloadPath: session.cover_letter?.download_url ?? null }
    }
  };
}

export async function postEvent(
  token: string,
  sessionId: number,
  event: { action_type: string; field_key?: string; status?: string; confidence?: number; metadata?: Record<string, unknown> }
): Promise<void> {
  await request(`/application-sessions/${sessionId}/events`, token, {
    method: "POST",
    body: JSON.stringify({ source: "extension", ...event })
  });
}

export async function patchStatus(token: string, sessionId: number, status: string): Promise<void> {
  await request(`/application-sessions/${sessionId}/status`, token, {
    method: "PATCH",
    body: JSON.stringify({ status })
  });
}

/** Record a safe, PII-free autofill result summary for the tracker/debugging. */
export async function reportAutofillResult(
  token: string,
  sessionId: number,
  result: AutofillResult
): Promise<void> {
  await request(`/application-sessions/${sessionId}/autofill-results`, token, {
    method: "POST",
    body: JSON.stringify(result)
  });
}

/** "Save for future applications" — the ONLY answer-vault write the extension
 * can make (it only ever holds a session-scoped token). Always the result of
 * an explicit user confirmation in the review widget. */
export async function saveSessionAnswer(
  token: string,
  sessionId: number,
  canonicalKey: string,
  body: { value: string; display_value?: string; scope?: string; company_key?: string }
): Promise<{ ok: boolean }> {
  return request(`/application-sessions/${sessionId}/answers/${encodeURIComponent(canonicalKey)}`, token, {
    method: "PUT",
    body: JSON.stringify(body)
  });
}

export interface StructuredName {
  firstName: string;
  lastName: string;
  middleName?: string;
  preferredFirstName?: string;
  preferredLastName?: string;
}

/** Explicit structured-name confirmation — never inferred.
 *
 * Sends the legacy given_name/family_name keys alongside the new ones so an
 * extension update rolled out ahead of the API still works. */
export async function confirmSessionName(
  token: string,
  sessionId: number,
  name: StructuredName
): Promise<{ ok: boolean }> {
  return request(`/application-sessions/${sessionId}/profile/name`, token, {
    method: "PUT",
    body: JSON.stringify({
      first_name: name.firstName,
      middle_name: name.middleName || null,
      last_name: name.lastName,
      preferred_first_name: name.preferredFirstName || null,
      preferred_last_name: name.preferredLastName || null,
      given_name: name.firstName,
      family_name: name.lastName
    })
  });
}

/** Mark the session complete — only ever on explicit user confirmation. */
export async function completeSession(token: string, sessionId: number): Promise<void> {
  await request(`/application-sessions/${sessionId}/complete`, token, {
    method: "POST",
    body: JSON.stringify({ confirmed: true })
  });
}

/** Fetch a generated document as a File for upload into the employer form. */
export async function fetchDocumentFile(
  token: string,
  sessionId: number,
  kind: "resume" | "cover-letter",
  filename: string
): Promise<File> {
  const base = await getApiBase();
  const res = await fetch(`${base}/application-sessions/${sessionId}/${kind}?fmt=pdf`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) {
    throw new Error(`Document fetch failed (${res.status})`);
  }
  const blob = await res.blob();
  return new File([blob], filename, { type: blob.type || "application/pdf" });
}
