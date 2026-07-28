import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TrackerClient } from "../components/TrackerClient";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

const APPLICATIONS = [
  {
    id: 11,
    job_id: 101,
    status: "applied",
    applied_at: "2026-07-20T12:00:00Z",
    created_at: "2026-07-20T12:00:00Z",
    updated_at: "2026-07-20T12:00:00Z",
    job: {
      id: 101,
      title: "Software Engineer",
      company: "Acme",
      company_domain: "acme.com",
      company_logo_url: null,
      company_logo_proxy_path: null,
      location: "New York, NY",
      workplace_type: "hybrid",
      employment_type: "Full-time",
      posted_at: "2026-07-18T12:00:00Z",
      application_url: "https://careers.acme.com/101",
      match: { fit_score: 84 }
    }
  },
  {
    id: 12,
    job_id: 102,
    status: "interview",
    applied_at: "2026-07-19T12:00:00Z",
    created_at: "2026-07-19T12:00:00Z",
    updated_at: "2026-07-21T12:00:00Z",
    job: {
      id: 102,
      title: "Backend Engineer",
      company: "Northstar",
      company_domain: "northstar.dev",
      company_logo_url: null,
      company_logo_proxy_path: null,
      location: "Remote",
      workplace_type: "remote",
      employment_type: "Full-time",
      posted_at: "2026-07-17T12:00:00Z",
      application_url: "https://northstar.dev/jobs/102",
      match: { fit_score: 91 }
    }
  }
];

describe("TrackerClient", () => {
  beforeEach(() => {
    localStorage.setItem("jobpilot_token", "token");
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.endsWith("/jobs/tracker/submitted")) {
        return Promise.resolve(jsonResponse({ applications: APPLICATIONS }));
      }
      if (url.endsWith("/jobs/101/tracker") && init?.method === "PUT") {
        return Promise.resolve(
          jsonResponse({ tracker: { id: 11, job_id: 101, status: "offer" } })
        );
      }
      return Promise.resolve(jsonResponse({}));
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loads automatically and presents useful job information instead of IDs", async () => {
    render(React.createElement(TrackerClient));

    expect(await screen.findByText("Software Engineer")).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Northstar")).toBeInTheDocument();
    expect(screen.getByText("Total tracked")).toBeInTheDocument();
    expect(screen.getAllByText("Applied").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Load applications/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /View job/i })[0]).toHaveAttribute(
      "href",
      "https://careers.acme.com/101"
    );
  });

  it("updates status from the application card", async () => {
    render(React.createElement(TrackerClient));
    const select = await screen.findByLabelText("Update status for Software Engineer");
    await userEvent.selectOptions(select, "offer");

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "http://localhost:8000/jobs/101/tracker",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ status: "offer" })
        })
      )
    );
    expect(await screen.findByText("Moved to Offer / selected.")).toBeInTheDocument();
  });

  it("filters applications by stage", async () => {
    render(React.createElement(TrackerClient));
    await screen.findByText("Software Engineer");

    await userEvent.click(screen.getByRole("button", { name: "Interviews" }));
    expect(screen.getByText("Backend Engineer")).toBeInTheDocument();
    expect(screen.queryByText("Software Engineer")).not.toBeInTheDocument();
  });
});
