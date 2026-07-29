import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "../components/AppShell";
import { JobDiscovery } from "../components/JobDiscovery";
import { clearPeopleCache } from "../lib/peopleClient";

// AppShell highlights the active section from the pathname.
vi.mock("next/navigation", () => ({ usePathname: () => "/jobs" }));

/**
 * The Jobs master-detail workspace: one screen that holds the list and the open
 * job, with the open job in the URL.
 */

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function isoDaysAgo(days: number) {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

const NOT_STARTED_PEOPLE = {
  status: "not_started",
  beta: true,
  categories: { likely_recruiters: [], potential_hiring_managers: [], potential_referrers: [] },
  warnings: [],
  quota: { daily_limit: 20, daily_used: 8, daily_remaining: 12, resets_at: "2026-07-30T00:00:00.000Z" },
  controls: { email_discovery: true, outreach_drafting: true }
};

const RECRUITER = {
  recommendation_id: 41,
  full_name: "Rita Recruiter",
  current_title: "Senior Technical Recruiter",
  current_company: "Acme",
  category: "likely_recruiter",
  category_label: "Likely recruiter",
  relevance_score: 88,
  confidence: "high",
  current_employment_confidence: 0.95,
  employment_validation_status: "confirmed_exact_company_verified",
  employment_last_verified_at: "2026-07-25T12:00:00Z",
  employment_warning: null,
  email_lookup_allowed: true,
  reasons: ["Currently listed at the hiring company."],
  limitations: [],
  last_checked_at: "2026-07-25T12:00:00Z",
  professional_profile_url: "https://www.linkedin.com/in/rita-recruiter",
  email_status: "not_requested",
  professional_email: null,
  email_verified_at: null,
  saved: false,
  contacted: false
};

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    title: "Machine Learning Engineer",
    company: "Acme",
    company_domain: "",
    company_logo_url: "",
    source: "greenhouse",
    location: "Remote, United States",
    location_display: "Remote",
    workplace_type: "remote",
    employment_type: "full-time",
    seniority_level: "mid",
    posted_at: isoDaysAgo(2),
    discovered_at: isoDaysAgo(0),
    application_url: "https://job-boards.greenhouse.io/acme/1",
    source_url: "https://job-boards.greenhouse.io/acme/1",
    description_clean: "Build ML systems.\n\n- Ship models\n- Own reliability",
    required_skills: ["Python", "PyTorch", "Kubernetes"],
    preferred_skills: [],
    responsibilities: ["Ship models"],
    salary_min: 150000,
    salary_max: 190000,
    salary_currency: "USD",
    match: {
      fit_score: 92,
      fit_label: "Strong fit",
      score_state: "scored",
      match_reasons: ["Title aligns with your target role"],
      missing_skills: ["Kubernetes"],
      risk_factors: [],
      recommended_resume_angle: "Lead with Python, PyTorch.",
      confidence: 0.8,
      explanation_source: "deterministic",
      fit_summary: "Strong overlap with your production Python work."
    },
    ...overrides
  };
}

// Distinct posted dates keep the newest-first order deterministic, so index
// assertions (the compact list, "2 of 5", previous/next) are stable.
const FIVE_JOBS = [
  job({ posted_at: isoDaysAgo(1) }),
  job({
    id: 2,
    title: "Data Platform Engineer",
    company: "Northwind",
    salary_min: null,
    salary_max: null,
    posted_at: isoDaysAgo(2)
  }),
  job({ id: 3, title: "Backend Engineer", company: "Contoso", posted_at: isoDaysAgo(3) }),
  job({ id: 4, title: "Research Engineer", company: "Globex", posted_at: isoDaysAgo(4) }),
  job({ id: 5, title: "Platform Engineer", company: "Initech", posted_at: isoDaysAgo(5) })
];

type Options = {
  jobs?: unknown[];
  people?: Record<number, unknown>;
  peopleDiscovered?: unknown;
  jobById?: Record<number, unknown>;
  jobByIdStatus?: number;
};

function mockApi(options: Options = {}) {
  const calls: string[] = [];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = new URL(String(input), "http://localhost:8000");
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url.pathname}`);

    if (url.pathname.startsWith("/jobs/tracker/")) {
      return Promise.resolve(jsonResponse({ applications: [] }));
    }
    if (url.pathname === "/jobs") {
      return Promise.resolve(
        jsonResponse({
          profile_complete: true,
          criteria: { role_queries: [], skills: [], seniority_targets: [] },
          profile_filters: { target_roles: [], target_levels: [], preferred_locations: [], work_preference: "remote" },
          jobs: options.jobs ?? FIVE_JOBS
        })
      );
    }
    const peopleMatch = url.pathname.match(/^\/jobs\/(\d+)\/people$/);
    if (peopleMatch) {
      return Promise.resolve(jsonResponse(options.people?.[Number(peopleMatch[1])] ?? NOT_STARTED_PEOPLE));
    }
    if (url.pathname.endsWith("/people/discover")) {
      return Promise.resolve(
        jsonResponse(
          options.peopleDiscovered ?? {
            ...NOT_STARTED_PEOPLE,
            status: "complete",
            categories: {
              likely_recruiters: [RECRUITER],
              potential_hiring_managers: [],
              potential_referrers: []
            },
            quota: { ...NOT_STARTED_PEOPLE.quota, daily_used: 9, daily_remaining: 11 }
          }
        )
      );
    }
    if (url.pathname.match(/\/generate-resume$/)) {
      return Promise.resolve(
        jsonResponse({
          document_id: 7,
          document_type: "resume",
          title: "Tailored Resume - Acme",
          content: { header: { full_name: "Chandra Pandey", email: "", phone: "", location: "", links: [] }, summary: "S", skills: [], experience: [], projects: [], education: [], awards: [] },
          markdown: "",
          plain_text: "",
          quality: { warnings: [] },
          warnings: [],
          unsupported_claims_removed: [],
          download_urls: { docx: null, pdf: null }
        })
      );
    }
    if (url.pathname.match(/\/generate-cover-letter$/)) {
      return Promise.resolve(
        jsonResponse({
          document_id: 8,
          document_type: "cover_letter",
          title: "Cover Letter - Acme",
          content: { date: "", recipient: "Hiring Team", company: "Acme", role: "MLE", greeting: "Dear Hiring Team,", paragraphs: ["Body."], closing: "Best regards,", signature: "Chandra Pandey" },
          markdown: "",
          plain_text: "",
          quality: { warnings: [] },
          warnings: [],
          unsupported_claims_removed: [],
          download_urls: { docx: null, pdf: null }
        })
      );
    }
    const byId = url.pathname.match(/^\/jobs\/(\d+)$/);
    if (byId) {
      const found = options.jobById?.[Number(byId[1])];
      if (!found) {
        return Promise.resolve(jsonResponse({ detail: "Job not found" }, options.jobByIdStatus ?? 404));
      }
      return Promise.resolve(jsonResponse({ job: found }));
    }
    return Promise.resolve(jsonResponse({}));
  });
  return { fetchMock, calls, peopleCalls: () => calls.filter((call) => call.includes("/people")) };
}

function renderWorkspace(search = "") {
  window.history.replaceState(null, "", `/jobs${search}`);
  return render(
    <AppShell workspace>
      <JobDiscovery />
    </AppShell>
  );
}

const sidebar = () => screen.getByRole("complementary", { name: "Primary" });
const cardOf = (title: string) => screen.getByText(title).closest("article") as HTMLElement;

describe("Jobs workspace", () => {
  beforeEach(() => {
    cleanup();
    clearPeopleCache();
    localStorage.setItem("jobpilot_token", "token");
    vi.restoreAllMocks();
  });

  it("starts as a list with the full sidebar and no job selected", async () => {
    mockApi();
    renderWorkspace();

    expect(await screen.findByRole("heading", { level: 1, name: "Jobs" })).toBeInTheDocument();
    expect(sidebar()).toHaveAttribute("data-collapsed", "false");
    expect(within(sidebar()).getByText("JobPilot AI")).toBeInTheDocument();
    expect(screen.getAllByTestId("job-card")).toHaveLength(5);
    expect(screen.queryByRole("tablist", { name: "Job sections" })).not.toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("mounts no job-detail or people request for a large list", async () => {
    const many = Array.from({ length: 142 }, (_value, index) =>
      job({ id: 100 + index, title: `Role ${index}` })
    );
    const api = mockApi({ jobs: many });
    renderWorkspace();

    await screen.findByText("Role 0");
    expect(screen.getAllByTestId("job-card")).toHaveLength(142);
    expect(api.peopleCalls()).toEqual([]);
    // Only the list and the tracker ledger — never one request per card.
    expect(api.calls.filter((call) => /^GET \/jobs\/\d+$/.test(call))).toEqual([]);
    expect(api.calls).toEqual(["GET /jobs", "GET /jobs/tracker/all"]);
  });

  it("opens the job from a click on the card body and reflects it in the URL", async () => {
    mockApi();
    renderWorkspace();

    await screen.findByText("Data Platform Engineer");
    await userEvent.click(cardOf("Data Platform Engineer"));

    expect(window.location.search).toBe("?job=2");
    expect(await screen.findByRole("heading", { level: 1, name: "Data Platform Engineer" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Showing Data Platform Engineer at Northwind");
  });

  it("collapses the sidebar to the brand rail and can expand it again", async () => {
    mockApi();
    renderWorkspace();

    await screen.findByText("Machine Learning Engineer");
    expect(sidebar()).toHaveAttribute("data-collapsed", "false");

    await userEvent.click(cardOf("Machine Learning Engineer"));

    await waitFor(() => expect(sidebar()).toHaveAttribute("data-collapsed", "true"));
    expect(sidebar().className).toContain("w-14");
    // Branding and navigation stay reachable, with accessible names.
    expect(within(sidebar()).getByLabelText("JobPilot AI home")).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "Jobs" })).toHaveAttribute("aria-current", "page");
    expect(within(sidebar()).getByRole("link", { name: "Settings" })).toBeInTheDocument();

    await userEvent.click(within(sidebar()).getByRole("button", { name: "Expand sidebar" }));
    expect(sidebar()).toHaveAttribute("data-collapsed", "false");
  });

  it("keeps a compact list beside the detail panel and switches jobs from it", async () => {
    mockApi();
    renderWorkspace();

    await screen.findByText("Machine Learning Engineer");
    await userEvent.click(cardOf("Machine Learning Engineer"));

    const compact = await screen.findAllByTestId("compact-job-card");
    expect(compact).toHaveLength(5);
    expect(compact[0]).toHaveAttribute("aria-current", "true");
    expect(compact[0]).toHaveAttribute("data-selected", "true");
    expect(compact[1]).not.toHaveAttribute("aria-current");
    // The compact card is a summary: no inline actions, no match explanation.
    expect(within(compact[0]).queryByText("Title aligns with your target role")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find people" })).not.toBeInTheDocument();

    await userEvent.click(compact[2]);

    expect(window.location.search).toBe("?job=3");
    expect(await screen.findByRole("heading", { level: 1, name: "Backend Engineer" })).toBeInTheDocument();
    // The previous job's content is gone, not left behind under the new title.
    expect(screen.queryByRole("heading", { level: 1, name: "Machine Learning Engineer" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Contoso").length).toBeGreaterThan(0);
  });

  it("opens a job with the keyboard using Enter and Space", async () => {
    mockApi();
    renderWorkspace();

    await screen.findByText("Backend Engineer");
    const title = screen.getByRole("button", { name: "Backend Engineer" });
    title.focus();
    expect(title).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(window.location.search).toBe("?job=3");

    await userEvent.click(screen.getByRole("button", { name: "Close job details" }));
    await screen.findByText("Research Engineer");
    screen.getByRole("button", { name: "Research Engineer" }).focus();
    await userEvent.keyboard(" ");
    expect(window.location.search).toBe("?job=4");
  });

  it("restores the open job from the URL on load", async () => {
    mockApi();
    renderWorkspace("?job=3&q=engineer&fit=60");

    expect(await screen.findByRole("heading", { level: 1, name: "Backend Engineer" })).toBeInTheDocument();
    expect(sidebar()).toHaveAttribute("data-collapsed", "true");
  });

  it("closing the detail panel returns to the list and keeps the filters", async () => {
    mockApi();
    renderWorkspace("?job=3&q=engineer&posted=14");

    await screen.findByRole("heading", { level: 1, name: "Backend Engineer" });
    await userEvent.click(screen.getByRole("button", { name: "Close job details" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Jobs" })).toBeInTheDocument();
    expect(window.location.search).not.toContain("job=");
    expect(window.location.search).toContain("q=engineer");
    expect(window.location.search).toContain("posted=14");
    expect(screen.getByLabelText("Role or skill")).toHaveValue("engineer");
    expect(sidebar()).toHaveAttribute("data-collapsed", "false");
  });

  it("supports browser Back and Forward across the open job", async () => {
    mockApi();
    renderWorkspace();

    await screen.findByText("Machine Learning Engineer");
    await userEvent.click(cardOf("Machine Learning Engineer"));
    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });

    await act(async () => {
      window.history.back();
    });
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(await screen.findByRole("heading", { level: 1, name: "Jobs" })).toBeInTheDocument();

    await act(async () => {
      window.history.forward();
    });
    await waitFor(() => expect(window.location.search).toBe("?job=1"));
    expect(await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" })).toBeInTheDocument();
  });

  it("closes the detail panel with Escape", async () => {
    mockApi();
    renderWorkspace("?job=1");

    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });
    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("loads a deep-linked job that is not in the current list", async () => {
    const api = mockApi({ jobById: { 99: job({ id: 99, title: "Deep Linked Role", company: "Umbrella" }) } });
    renderWorkspace("?job=99");

    expect(await screen.findByRole("heading", { level: 1, name: "Deep Linked Role" })).toBeInTheDocument();
    expect(api.calls.filter((call) => call === "GET /jobs/99")).toHaveLength(1);
  });

  it("fails gracefully for a job that no longer exists", async () => {
    mockApi();
    renderWorkspace("?job=4242");

    expect(await screen.findByText("This job is not available")).toBeInTheDocument();
    expect(screen.getByText("This job is no longer listed.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Back to jobs/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Jobs" })).toBeInTheDocument();
  });

  it("shows an honest overview: fit signals, salary when present, and no invented sub-scores", async () => {
    mockApi();
    renderWorkspace("?job=1");

    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });
    expect(screen.getByText("Strong overlap with your production Python work.")).toBeInTheDocument();
    expect(screen.getAllByText("92").length).toBeGreaterThan(0);
    expect(screen.getByText("Title aligns with your target role")).toBeInTheDocument();
    expect(screen.getByText("2 of 3")).toBeInTheDocument(); // required skills matched
    expect(screen.getByText(/Category-level\s+sub-scores are not calculated/)).toBeInTheDocument();
    expect(screen.getAllByText("$150k–190k").length).toBeGreaterThan(0);
    // The matched/missing split is labelled as derived, never presented as a
    // deeper scoring model.
    expect(screen.getByText(/Derived from this job’s required skills/)).toBeInTheDocument();
  });

  it("omits salary entirely when the employer published none", async () => {
    mockApi();
    renderWorkspace("?job=2");

    await screen.findByRole("heading", { level: 1, name: "Data Platform Engineer" });
    expect(screen.queryByText(/\$\d/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Salary/)).not.toBeInTheDocument();
  });

  it("renders the description as text, never as markup, with a source link", async () => {
    mockApi({
      jobs: [job({ description_clean: "<script>alert(1)</script>Build ML systems.\n- Ship models" })]
    });
    renderWorkspace("?job=1");

    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });
    await userEvent.click(screen.getByRole("tab", { name: "Job description" }));

    expect(screen.getByText(/<script>alert\(1\)<\/script>Build ML systems\./)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getAllByRole("listitem").some((item) => item.textContent === "Ship models")).toBe(true);
    expect(screen.getByRole("link", { name: /Open original posting/ })).toHaveAttribute(
      "href",
      "https://job-boards.greenhouse.io/acme/1"
    );
  });

  it("generates a resume and a cover letter from the detail tabs", async () => {
    const api = mockApi();
    renderWorkspace("?job=1");

    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });
    await userEvent.click(screen.getByRole("tab", { name: "Resume" }));
    expect(screen.getByText("Not generated for this job yet.")).toBeInTheDocument();
    expect(screen.getByText("Lead with Python, PyTorch.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Generate tailored resume" }));
    expect(await screen.findByRole("heading", { name: "Tailored Resume" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close document" }));
    expect(await screen.findByText("Ready: Tailored Resume - Acme")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview and download" })).toBeInTheDocument();
    expect(api.calls.filter((call) => call.endsWith("/generate-resume"))).toHaveLength(1);

    await userEvent.click(screen.getByRole("tab", { name: "Cover letter" }));
    await userEvent.click(screen.getByRole("button", { name: "Generate cover letter" }));
    expect(await screen.findByRole("heading", { name: "Cover Letter" })).toBeInTheDocument();
    expect(api.calls.filter((call) => call.endsWith("/generate-cover-letter"))).toHaveLength(1);
  });

  it("keeps Networking manual: one read on open, one search per explicit click", async () => {
    const api = mockApi();
    renderWorkspace("?job=1");

    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });
    expect(api.peopleCalls()).toEqual([]);

    await userEvent.click(screen.getByRole("tab", { name: "Networking" }));
    await waitFor(() => expect(api.peopleCalls()).toEqual(["GET /jobs/1/people"]));
    // Reading the allowance is free and visible before spending it.
    expect(await screen.findByText("12 of 20 people searches remaining today.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Find people" }));
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(api.peopleCalls().filter((call) => call.startsWith("POST"))).toEqual([
      "POST /jobs/1/people/discover"
    ]);
    expect(await screen.findByText(/11 searches remaining/)).toBeInTheDocument();
  });

  it("shows cached people results without searching, and the card reflects the count", async () => {
    mockApi({
      people: {
        1: {
          ...NOT_STARTED_PEOPLE,
          status: "complete",
          result_freshness: "fresh",
          quota: { ...NOT_STARTED_PEOPLE.quota, daily_used: 20, daily_remaining: 0 },
          categories: {
            likely_recruiters: [RECRUITER],
            potential_hiring_managers: [],
            potential_referrers: []
          }
        }
      }
    });
    const api = mockApi({
      people: {
        1: {
          ...NOT_STARTED_PEOPLE,
          status: "complete",
          result_freshness: "fresh",
          quota: { ...NOT_STARTED_PEOPLE.quota, daily_used: 20, daily_remaining: 0 },
          categories: {
            likely_recruiters: [RECRUITER],
            potential_hiring_managers: [],
            potential_referrers: []
          }
        }
      }
    });
    renderWorkspace("?job=1");

    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });
    await userEvent.click(screen.getByRole("tab", { name: "Networking" }));

    // Stored results stay visible at zero remaining searches.
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(screen.getByText("0 of 20 people searches remaining today.")).toBeInTheDocument();
    expect(api.peopleCalls().filter((call) => call.startsWith("POST"))).toEqual([]);

    // Back on the list, the card's People action reports the loaded count
    // without issuing a request of its own.
    const before = api.peopleCalls().length;
    await userEvent.click(screen.getByRole("button", { name: "Close job details" }));
    const card = cardOf("Machine Learning Engineer");
    expect(within(card).getByRole("button", { name: "1 person" })).toBeInTheDocument();
    expect(api.peopleCalls()).toHaveLength(before);
  });

  it("opens the Networking section directly from the card's People action, without searching", async () => {
    const api = mockApi();
    renderWorkspace();

    await screen.findByText("Machine Learning Engineer");
    const card = cardOf("Machine Learning Engineer");
    await userEvent.click(within(card).getByRole("button", { name: "Find people" }));

    expect(window.location.search).toBe("?job=1");
    expect(await screen.findByRole("heading", { name: "People Who Can Help" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Networking" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(api.peopleCalls()).toEqual(["GET /jobs/1/people"]));
  });

  it("moves between jobs with the previous and next controls", async () => {
    mockApi();
    renderWorkspace("?job=2");

    await screen.findByRole("heading", { level: 1, name: "Data Platform Engineer" });
    expect(screen.getByText("2 of 5")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next job" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Backend Engineer" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Previous job" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Data Platform Engineer" })).toBeInTheDocument();
  });

  it("saves and applies from the detail header", async () => {
    const api = mockApi();
    renderWorkspace("?job=1");

    await screen.findByRole("heading", { level: 1, name: "Machine Learning Engineer" });
    await userEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);

    await waitFor(() => expect(api.calls).toContain("POST /jobs/1/save"));
    expect((await screen.findAllByText("Saved to tracker")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /Apply on official site/ }).length).toBeGreaterThan(0);
  });
});
