/** Secure API client. Uses a session-scoped token (never the user's main login
 * token) and only ever touches endpoints for the one application session. */

import { getApiBase } from "../config";
import type { AutofillResult } from "../messages";
import type { ApplicationSessionData, SessionAnswer } from "../types";

async function request<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const base = await getApiBase();
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...init.headers }
  });
  if (!res.ok) {
    throw new Error(`API ${res.status} for ${path}`);
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
    throw new Error(`Token exchange failed (${res.status})`);
  }
  return (await res.json()) as { session_token: string; session: RawSession };
}

type RawSession = {
  session_id: number;
  ats_type: string | null;
  official_application_url: string;
  job?: { title: string | null; company: string | null };
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
    answers: answers.answers,
    unresolvedQuestions: answers.unresolved_questions
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
