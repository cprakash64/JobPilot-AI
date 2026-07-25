/**
 * Part 5 — session API compatibility: proves the extension's client can fetch
 * and parse a session package shaped exactly like the CURRENT backend's real
 * response (apps/api/app/routes/applications.py's _serialize_session /
 * get_session_answers, including the newer unresolved_questions fields added
 * for structured-name confirmation and company-scoped answers). An unknown
 * optional field must never crash the package loader.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { exchangeLaunchToken, fetchSessionData } from "../api/client";

function mockFetch(handlers: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      for (const [suffix, body] of Object.entries(handlers)) {
        if (url.endsWith(suffix)) return new Response(JSON.stringify(body), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    })
  );
}

describe("session package parsing (current backend response shape)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("exchanges a launch token for a session-scoped token", async () => {
    mockFetch({ "/application-sessions/token": { session_token: "sess-tok-abc", session: {} } });
    const { session_token } = await exchangeLaunchToken("launch-tok");
    expect(session_token).toBe("sess-tok-abc");
  });

  it("parses a session package with the current backend's answer + unresolved_questions shape, including the newer name-confirmation and company-scope fields", async () => {
    mockFetch({
      "/application-sessions/55": {
        session_id: 55,
        ats_type: "greenhouse",
        official_application_url: "https://boards.greenhouse.io/affirm/jobs/1",
        job: { title: "Backend Engineer", company: "Affirm" },
        resume: { status: "ready", document_id: 7, download_url: "/application-sessions/55/resume" },
        cover_letter: { status: "ready", document_id: 8, download_url: "/application-sessions/55/cover-letter" },
        profile: { full_name: "Chandra Prakash Pandey" }
      },
      "/application-sessions/55/answers": {
        answers: [
          { canonical_key: "email", value: "cp@example.com", display_value: "cp@example.com", source: "profile", confidence: 0.97, sensitive: false, requires_review: false, verified: true }
        ],
        unresolved_questions: [
          // Structured-name confirmation shape (new fields the extension must
          // tolerate even if it doesn't render them yet).
          { canonical_key: "first_name", reason: "Confirm your name.", sensitive: false, action: "confirm_name", suggested_value: "Chandra", has_saved_value: false },
          { canonical_key: "gender", reason: "Demographic (voluntary EEO)", sensitive: true, action: "answer_on_employer_page", has_saved_value: false },
          // A hypothetical future field the extension has never heard of —
          // must be ignored, never crash the loader.
          { canonical_key: "referral_source", reason: "Company-specific", sensitive: false, some_future_field: 42 }
        ]
      }
    });

    const session = await fetchSessionData("sess-tok-abc", 55);
    expect(session.sessionId).toBe(55);
    expect(session.atsType).toBe("greenhouse");
    expect(session.company).toBe("Affirm");
    expect(session.answers).toHaveLength(1);
    expect(session.answers[0].canonical_key).toBe("email");
    expect(session.unresolvedQuestions).toHaveLength(3);
    expect(session.unresolvedQuestions[0].action).toBe("confirm_name");
    expect(session.unresolvedQuestions[0].suggested_value).toBe("Chandra");
    expect(session.documents?.resume.status).toBe("ready");
  });

  it("never crashes when optional document/profile fields are absent (degraded session)", async () => {
    mockFetch({
      "/application-sessions/56": {
        session_id: 56,
        ats_type: null,
        official_application_url: "https://careers.example.com/apply/1"
        // no job, resume, cover_letter, profile — all optional per the client's RawSession type
      },
      "/application-sessions/56/answers": { answers: [], unresolved_questions: [] }
    });
    const session = await fetchSessionData("sess-tok-abc", 56);
    expect(session.sessionId).toBe(56);
    expect(session.jobTitle).toBeNull();
    expect(session.company).toBeNull();
    expect(session.documents?.resume.status).toBe("missing");
  });
});
