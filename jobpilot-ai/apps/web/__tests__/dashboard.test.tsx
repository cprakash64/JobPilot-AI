import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardClient } from "../components/DashboardClient";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
}

describe("DashboardClient", () => {
  beforeEach(() => {
    localStorage.setItem("jobpilot_token", "token");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/profile")) {
        return Promise.resolve(jsonResponse({
          profile: {
            first_name: "Chandra",
            application_email: "chandra@example.com",
            target_roles: ["Software Engineer"],
            preferred_locations: ["Remote"],
            skills: ["TypeScript"]
          }
        }));
      }
      if (url.endsWith("/jobs/tracker/all")) {
        return Promise.resolve(jsonResponse({
          applications: [
            {
              id: 1,
              job_id: 10,
              status: "interview",
              job: { id: 10, title: "Platform Engineer", company: "Northstar" }
            }
          ]
        }));
      }
      if (url.includes("/jobs?posted_within_days=7")) {
        return Promise.resolve(jsonResponse({
          profile_complete: true,
          jobs: [
            {
              id: 22,
              title: "Senior Software Engineer",
              company: "Acme",
              location: "Remote",
              match: { fit_score: 91 }
            }
          ]
        }));
      }
      return Promise.resolve(jsonResponse({}));
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows live pipeline, recent activity, and best matches", async () => {
    render(React.createElement(DashboardClient));

    expect(await screen.findByText("Welcome back, Chandra.")).toBeInTheDocument();
    expect(screen.getByText("Platform Engineer")).toBeInTheDocument();
    expect(screen.getByText("Senior Software Engineer")).toBeInTheDocument();
    expect(screen.getByText("91% fit")).toBeInTheDocument();
    expect(screen.getAllByText("Interviews")[0]).toBeInTheDocument();
  });
});
