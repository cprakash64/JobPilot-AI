import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Override only the extension-handoff bits; keep createApplicationSession real
// so it exercises the fetch client.
vi.mock("@/lib/autoApply", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/autoApply")>();
  return {
    ...actual,
    detectExtension: vi.fn().mockResolvedValue(false),
    detectExtensionState: vi.fn(),
    handoffToExtension: vi.fn(),
    openOfficialSite: vi.fn().mockReturnValue({} as Window)
  };
});

import { AutoApplyModal } from "../components/AutoApplyModal";
import * as autoApply from "@/lib/autoApply";

const EXT_CONNECTED = {
  present: true as const,
  outdated: false,
  info: { installed: true as const, version: "0.1.0", protocolVersion: 1, capabilities: ["fill", "upload"] }
};
const EXT_ABSENT = { present: false as const };
const EXT_OUTDATED = {
  present: true as const,
  outdated: true,
  info: { installed: true as const, version: "0.0.1", protocolVersion: 0, capabilities: [] }
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const SESSION = {
  session_id: 55,
  status: "ready",
  official_application_url: "https://boards.greenhouse.io/acme/1",
  ats_type: "greenhouse",
  job: { id: 1, title: "Backend Engineer", company: "Acme", location: "Remote" },
  resume: { status: "ready", document_id: 7, download_url: "/application-sessions/55/resume" },
  cover_letter: { status: "ready", document_id: 8, download_url: "/application-sessions/55/cover-letter" },
  answers_available: 6,
  review_required_count: 2,
  unresolved_questions: [{ canonical_key: "gender", reason: "Demographic (voluntary EEO)" }],
  warnings: ["1 sensitive question must be answered by you on the employer page."],
  extension_launch_token: "launch-token-abc"
};

function mockFetch() {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.endsWith("/application-sessions") && method === "POST") {
      return Promise.resolve(jsonResponse(SESSION, 201));
    }
    if (url.includes("/application-sessions/55/status") && method === "PATCH") {
      return Promise.resolve(jsonResponse({ ...SESSION, status: "opened" }));
    }
    return Promise.resolve(jsonResponse({}));
  });
}

const OFFICIAL_URL = "https://boards.greenhouse.io/acme/1";

function renderModal(overrides: Partial<Record<string, unknown>> = {}) {
  return render(
    React.createElement(AutoApplyModal, {
      jobId: 1,
      jobTitle: "Backend Engineer",
      company: "Acme",
      officialUrl: OFFICIAL_URL,
      onClose: () => {},
      ...overrides
    })
  );
}

describe("AutoApplyModal", () => {
  beforeEach(() => {
    localStorage.setItem("jobpilot_token", "token");
    vi.clearAllMocks();
    // Default: extension connected. Individual tests override before rendering.
    vi.mocked(autoApply.detectExtensionState).mockResolvedValue(EXT_CONNECTED);
    mockFetch();
  });
  afterEach(cleanup);

  it("calls the correct API URL with auth header when preparing", async () => {
    renderModal();
    await screen.findByText("Tailored resume");
    // At least one test must assert the final requested URL (not mocked away).
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/application-sessions",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer token" })
      })
    );
  });

  it("prepares a session and shows resume, cover letter, answers, and review items", async () => {
    renderModal();
    expect(await screen.findByText("Tailored resume")).toBeInTheDocument();
    expect(screen.getByText("Tailored cover letter")).toBeInTheDocument();
    expect(screen.getByText(/6 verified answers ready to fill/)).toBeInTheDocument();
    expect(screen.getByText(/2 items need your review/)).toBeInTheDocument();
    expect(screen.getByText(/never submits it/)).toBeInTheDocument();
  });

  it("shows an install prompt when the extension is not detected but still allows opening the site", async () => {
    vi.mocked(autoApply.detectExtensionState).mockResolvedValue(EXT_ABSENT);
    renderModal();
    expect(await screen.findByText(/Install the JobPilot browser extension/i)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Install extension/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Open manually/ })).toHaveAttribute("href", OFFICIAL_URL);
    // No extension -> the primary CTA is the plain manual open, no autofill claim.
    expect(await screen.findByRole("button", { name: /Open official application/i })).toBeInTheDocument();
  });

  it("shows a connected state and an autofill CTA when the extension is present", async () => {
    renderModal();
    expect(await screen.findByText(/JobPilot extension connected/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open and autofill application/i })).toBeInTheDocument();
  });

  it("shows an update prompt when the extension protocol is too old", async () => {
    vi.mocked(autoApply.detectExtensionState).mockResolvedValue(EXT_OUTDATED);
    renderModal();
    expect(await screen.findByText(/extension needs an update/i)).toBeInTheDocument();
    // Outdated is treated as not-connected: no autofill claim, manual open only.
    expect(screen.getByRole("button", { name: /Open official application/i })).toBeInTheDocument();
  });

  it("hands off the launch token and opens the official site on click when connected", async () => {
    renderModal();
    const openBtn = await screen.findByRole("button", { name: /Open and autofill application/i });
    await userEvent.click(openBtn);

    await waitFor(() => expect(autoApply.handoffToExtension).toHaveBeenCalledWith("launch-token-abc", expect.any(Object)));
    expect(autoApply.openOfficialSite).toHaveBeenCalledWith(OFFICIAL_URL);
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/application-sessions/55/status"),
        expect.objectContaining({ method: "PATCH" })
      )
    );
  });

  it("does not hand off the launch token when the extension is absent", async () => {
    vi.mocked(autoApply.detectExtensionState).mockResolvedValue(EXT_ABSENT);
    renderModal();
    const openBtn = await screen.findByRole("button", { name: /Open official application/i });
    await userEvent.click(openBtn);
    expect(autoApply.openOfficialSite).toHaveBeenCalledWith(OFFICIAL_URL);
    expect(autoApply.handoffToExtension).not.toHaveBeenCalled();
  });

  it("shows a backend-unreachable message when fetch rejects (Failed to fetch)", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    renderModal();
    expect(await screen.findByText(/could not reach the application service/i)).toBeInTheDocument();
    // The manual fallback + retry remain available.
    expect(screen.getByRole("button", { name: /Retry preparation/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open official application/ })).toBeInTheDocument();
  });

  it("shows a session-expired message on 401 and hides Retry (non-retryable)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Invalid token" }), { status: 401 })
    );
    renderModal();
    expect(await screen.findByText(/session expired/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry preparation/ })).not.toBeInTheDocument();
  });

  it("renders a structured PROFILE_INCOMPLETE error with a profile link and no Retry", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "PROFILE_INCOMPLETE",
            message: "Complete your profile before preparing an application. Add your skills.",
            stage: "load_candidate_profile",
            retryable: false,
            request_id: "abc123"
          }
        }),
        { status: 422, headers: { "Content-Type": "application/json" } }
      )
    );
    renderModal();
    expect(await screen.findByText(/Complete your profile/)).toBeInTheDocument();
    // Non-retryable → no Retry button, but a shortcut to the profile is offered.
    expect(screen.queryByRole("button", { name: /Retry preparation/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Go to your profile/ })).toHaveAttribute("href", "/profile");
    // The correlation id is surfaced for support without exposing internals.
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
  });

  it("renders a structured DATABASE_UNAVAILABLE error as retryable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "DATABASE_UNAVAILABLE",
            message: "The application service is temporarily unavailable. Please try again.",
            stage: "persist_application_package",
            retryable: true
          }
        }),
        { status: 503, headers: { "Content-Type": "application/json" } }
      )
    );
    renderModal();
    expect(await screen.findByText(/temporarily unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry preparation/ })).toBeInTheDocument();
  });

  it("shows the backend validation message on 422", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "This job has no official application URL." }), { status: 422 })
    );
    renderModal();
    expect(await screen.findByText(/no official application URL/)).toBeInTheDocument();
  });

  it("shows a retryable message on 503 and Retry triggers a new request", async () => {
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.endsWith("/application-sessions") && (init?.method ?? "GET") === "POST") {
        calls += 1;
        if (calls === 1) {
          return Promise.resolve(
            new Response(JSON.stringify({ detail: "Database unavailable or not migrated. Run `alembic upgrade head`." }), { status: 503 })
          );
        }
        return Promise.resolve(jsonResponse(SESSION, 201));
      }
      return Promise.resolve(jsonResponse({}));
    });

    renderModal();
    expect(await screen.findByText(/Database unavailable or not migrated/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Retry preparation/ }));
    // Second attempt succeeds → ready state.
    expect(await screen.findByText("Tailored resume")).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it("does not issue duplicate preparation requests while one is in flight", async () => {
    let postCount = 0;
    const deferred: { resolve?: (r: Response) => void } = {};
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.endsWith("/application-sessions") && (init?.method ?? "GET") === "POST") {
        postCount += 1;
        return new Promise<Response>((resolve) => {
          deferred.resolve = resolve;
        });
      }
      return Promise.resolve(jsonResponse({}));
    });

    renderModal();
    // While the request is pending the loading checklist is shown and there is no
    // Retry button to re-trigger a second request.
    expect(await screen.findByText(/Preparing your verified profile/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry preparation/ })).not.toBeInTheDocument();
    expect(postCount).toBe(1);

    deferred.resolve?.(jsonResponse(SESSION, 201));
    expect(await screen.findByText("Tailored resume")).toBeInTheDocument();
    expect(postCount).toBe(1);
  });

  it("handles a blocked popup by keeping a manual link", async () => {
    (autoApply.openOfficialSite as unknown as ReturnType<typeof vi.fn>).mockReturnValueOnce(null);
    renderModal();
    await userEvent.click(await screen.findByRole("button", { name: /Open and autofill application/i }));
    expect(await screen.findByText(/browser blocked the new tab/i)).toBeInTheDocument();
  });
});
