import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { JobDiscovery } from "../components/JobDiscovery";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function isoDaysAgo(days: number) {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

function makeJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    title: "Machine Learning Engineer",
    company: "Acme",
    company_domain: "",
    company_logo_url: "",
    source: "greenhouse",
    location: "Remote",
    workplace_type: "remote",
    employment_type: "full-time",
    seniority_level: "mid",
    posted_at: isoDaysAgo(2),
    discovered_at: isoDaysAgo(0),
    application_url: "https://job-boards.greenhouse.io/acme/1",
    source_url: "https://job-boards.greenhouse.io/acme/1",
    description_clean: "Build ML systems.",
    required_skills: ["Python", "PyTorch"],
    preferred_skills: [],
    responsibilities: ["Ship models"],
    match: {
      fit_score: 92,
      fit_label: "Strong fit",
      match_reasons: ["Title aligns with your target role"],
      missing_skills: ["Kubernetes"],
      risk_factors: [],
      recommended_resume_angle: "Lead with Python, PyTorch.",
      confidence: 0.8,
      explanation_source: "deterministic"
    },
    ...overrides
  };
}

function mockJobsFetch(
  options: { profileComplete?: boolean; existing?: unknown[]; discovered?: unknown[]; resumeWarnings?: string[] } = {}
) {
  const profileComplete = options.profileComplete ?? true;
  const resumeWarnings = options.resumeWarnings ?? [
    "Generated with template mode because no OpenAI API key is configured."
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes("/jobs/discover")) {
      return Promise.resolve(
        jsonResponse({
          profile_complete: profileComplete,
          criteria: { role_queries: [], skills: [], seniority_targets: [] },
          profile_filters: { target_roles: [], target_levels: [], preferred_locations: [], work_preference: "remote" },
          jobs: options.discovered ?? [makeJob()],
          discovery: {
            fetched: 42, fresh: 12, persisted: 12, matched: 12, source_warnings: [],
            sources_searched: 30, sources_succeeded: 29, sources_failed: 1,
            excluded_location: 5, excluded_seniority: 3, excluded_role: 2,
            eligible: (options.discovered ?? [makeJob()]).length
          }
        })
      );
    }
    if (url.includes("/jobs/refresh-matches") && method === "POST") {
      return Promise.resolve(
        jsonResponse({
          profile_complete: profileComplete,
          criteria: {},
          jobs: options.discovered ?? [makeJob()],
          summary: {
            matched_count: 1, rescored_count: 8, raw_jobs_considered: 12,
            excluded_by_location: 3, excluded_by_seniority: 2, excluded_by_role: 1,
            excluded_by_date: 4, source_warnings: []
          }
        })
      );
    }
    if (url.match(/\/jobs\/\d+\/generate-resume/) || url.match(/\/jobs\/documents\/\d+$/)) {
      return Promise.resolve(
        jsonResponse({
          document_id: 7, document_type: "resume", title: "Tailored Resume - Acme - ML Engineer",
          content: {
            header: { full_name: "Chandra Pandey", email: "cp@example.com", phone: "602-816-1309", location: "Phoenix, AZ", links: ["https://linkedin.com/in/cp"] },
            summary: "Backend engineer with production Python and FastAPI experience.",
            skills: [{ category: "Core Skills", items: ["Python", "FastAPI", "PostgreSQL"] }],
            experience: [{ title: "ML Engineer Intern", company: "Cardinal Health", location: "AZ", dates: "2025", bullets: ["Built Python services"] }],
            projects: [{ name: "Luna AI", technologies: ["Python"], bullets: ["Built multimodal pipeline"] }],
            education: [{ school: "Arizona State University", degree: "BS", dates: "2025", details: "" }],
            awards: [], certifications: [], missing_skills: ["Kubernetes"]
          },
          markdown: "# Chandra Pandey", plain_text: "Chandra Pandey\nPROFESSIONAL SUMMARY\nBackend engineer...",
          quality: { ats_friendly: true, job_tailored: true, unsupported_claims_removed: [], missing_job_skills_not_claimed: ["Kubernetes"], estimated_page_count: 1, warnings: resumeWarnings },
          warnings: resumeWarnings,
          unsupported_claims_removed: [], download_urls: { docx: "/jobs/documents/7/download/docx", pdf: "/jobs/documents/7/download/pdf" }
        })
      );
    }
    if (url.match(/\/jobs\/\d+\/generate-cover-letter/)) {
      return Promise.resolve(
        jsonResponse({
          document_id: 8, document_type: "cover_letter", title: "Cover Letter - Acme - ML Engineer",
          content: {
            date: "July 10, 2026", recipient: "Hiring Team", company: "Acme", role: "ML Engineer",
            greeting: "Dear Hiring Team,",
            paragraphs: ["I am excited to apply for the ML Engineer role at Acme.", "My experience fits well.", "Thank you for your time."],
            closing: "Best regards,", signature: "Chandra Pandey"
          },
          markdown: "July 10, 2026", plain_text: "July 10, 2026\n\nHiring Team\nAcme",
          quality: { job_tailored: true, unsupported_claims_removed: [], word_count: 187, warnings: [] },
          warnings: [], unsupported_claims_removed: [], download_urls: { docx: "/jobs/documents/8/download/docx", pdf: "/jobs/documents/8/download/pdf" }
        })
      );
    }
    if (url.match(/\/jobs\/\d+\/save/)) {
      return Promise.resolve(jsonResponse({ tracker: { id: 1, job_id: 1, status: "saved" } }));
    }
    if (url.includes("/jobs")) {
      return Promise.resolve(
        jsonResponse({
          profile_complete: profileComplete,
          criteria: { role_queries: [], skills: [], seniority_targets: [] },
          profile_filters: { target_roles: [], target_levels: [], preferred_locations: [], work_preference: "remote" },
          jobs: options.existing ?? []
        })
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

describe("JobDiscovery", () => {
  beforeEach(() => {
    cleanup();
    localStorage.setItem("jobpilot_token", "token");
    vi.restoreAllMocks();
  });

  it("renders the primary CTAs and filters", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));

    expect(await screen.findByRole("button", { name: /Find fresh jobs/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refresh matches/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Role or skill")).toBeInTheDocument();
    expect(screen.getByLabelText("Workplace type")).toBeInTheDocument();
    expect(screen.getByLabelText("Minimum fit score")).toBeInTheDocument();
    expect(screen.getByLabelText("Posted within days")).toBeInTheDocument();
  });

  it("discovers jobs and shows fit score, posted date, and official apply link", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));

    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));

    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    expect(within(card).getByText("92")).toBeInTheDocument();
    expect(within(card).getByText("Strong fit")).toBeInTheDocument();
    expect(within(card).getByText("Posted 2 days ago")).toBeInTheDocument();
    const applyLink = within(card).getByRole("link", { name: /Apply on official site/ });
    expect(applyLink).toHaveAttribute("href", "https://job-boards.greenhouse.io/acme/1");
    expect(applyLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(applyLink).toHaveAttribute("target", "_blank");
    // Source badge shows the ATS provider name.
    expect(within(card).getByText("Greenhouse")).toBeInTheDocument();
  });

  it("never renders demo jobs, 'via demo', or example.com apply links", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));

    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    await screen.findByText("Machine Learning Engineer");

    expect(screen.queryByText(/DemoCo/)).not.toBeInTheDocument();
    expect(screen.queryByText(/via demo/i)).not.toBeInTheDocument();
    const exampleLinks = screen
      .queryAllByRole("link")
      .filter((link) => (link.getAttribute("href") ?? "").includes("example.com"));
    expect(exampleLinks).toHaveLength(0);
    // AI-unavailable is not the main experience; deterministic reasons are shown.
    expect(screen.queryByText(/AI explanation unavailable/i)).not.toBeInTheDocument();
    expect(screen.getByText("Title aligns with your target role")).toBeInTheDocument();
  });

  it("hides the apply link when the application URL is missing or a placeholder", async () => {
    mockJobsFetch({ discovered: [makeJob({ application_url: "https://example.com/apply" })] });
    render(React.createElement(JobDiscovery));

    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    expect(within(card).queryByRole("link", { name: /Apply on official site/ })).not.toBeInTheDocument();
    expect(within(card).getByText("No official link available")).toBeInTheDocument();
  });

  it("shows the complete-profile empty state when profile is incomplete", async () => {
    mockJobsFetch({ profileComplete: false });
    render(React.createElement(JobDiscovery));

    // Shown both as a banner and as the empty-state message.
    const messages = await screen.findAllByText("Complete your profile to discover better jobs.");
    expect(messages.length).toBeGreaterThanOrEqual(1);
  });

  it("shows a no-jobs state before discovering", async () => {
    mockJobsFetch({ existing: [] });
    render(React.createElement(JobDiscovery));

    expect(await screen.findByText(/Click .Find fresh jobs./)).toBeInTheDocument();
  });

  it("filters visible jobs by role text", async () => {
    mockJobsFetch({
      discovered: [
        makeJob({ id: 1, title: "Machine Learning Engineer" }),
        makeJob({ id: 2, title: "Warehouse Associate", required_skills: ["Forklift"], match: { ...makeJob().match, fit_score: 25, fit_label: "Low fit", missing_skills: [] } })
      ]
    });
    render(React.createElement(JobDiscovery));

    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    expect(await screen.findByText("Machine Learning Engineer")).toBeInTheDocument();
    expect(screen.getByText("Warehouse Associate")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Role or skill"), "warehouse");

    await waitFor(() => expect(screen.queryByText("Machine Learning Engineer")).not.toBeInTheDocument());
    expect(screen.getByText("Warehouse Associate")).toBeInTheDocument();
  });

  it("shows the eligibility criteria line", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    expect(
      await screen.findByText(/Filtered to your roles, level, and locations\./)
    ).toBeInTheDocument();
  });

  it("shows matched count and searched source count after discovery", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    await screen.findByText("Machine Learning Engineer");

    expect(screen.getByText(/Showing 1 matched job/)).toBeInTheDocument();
    expect(screen.getByText(/Searched 30 verified company sources/)).toBeInTheDocument();
    expect(screen.getByText(/Excluded by location: 5/)).toBeInTheDocument();
    expect(screen.getByText(/Excluded by seniority: 3/)).toBeInTheDocument();
  });

  it("shows the broaden-results helper when fewer than 10 jobs", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    expect(
      await screen.findByText("We found only a few strict matches. Add more target roles or locations to broaden results.")
    ).toBeInTheDocument();
  });

  it("renders only the server's eligible set (no Canada/senior/unrelated leak)", async () => {
    // The backend already filters; the page must render exactly what it returns.
    mockJobsFetch({
      discovered: [
        makeJob({ id: 1, title: "Backend Engineer", location: "Remote, United States" }),
        makeJob({ id: 2, title: "Machine Learning Engineer", location: "San Francisco, CA" })
      ]
    });
    render(React.createElement(JobDiscovery));

    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    await screen.findByText("Backend Engineer");

    expect(screen.queryByText(/Canada/)).not.toBeInTheDocument();
    expect(screen.queryByText(/India/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Senior Software Engineer/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Customer Success Engineer/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Social Measurement/)).not.toBeInTheDocument();
  });

  it("shows the filtered empty state when nothing is eligible", async () => {
    mockJobsFetch({ discovered: [] });
    render(React.createElement(JobDiscovery));

    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    expect(
      await screen.findByText(
        "No fresh jobs matched your selected roles, level, and locations. Try broadening your target roles or locations."
      )
    ).toBeInTheDocument();
  });

  it("refresh matches calls the endpoint and shows a success summary", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Refresh matches/ }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "http://localhost:8000/jobs/refresh-matches?posted_within_days=7",
        expect.objectContaining({ method: "POST" })
      )
    );
    expect(await screen.findByText(/Re-scored 8 jobs/)).toBeInTheDocument();
  });

  it("renders Resume and Cover Letter actions on each job card", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    expect(within(card).getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Cover Letter" })).toBeInTheDocument();
  });

  it("Resume button generates and opens the resume modal", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Resume" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "http://localhost:8000/jobs/1/generate-resume",
        expect.objectContaining({ method: "POST" })
      )
    );
    // Preview mode is the default: a rendered document, not a raw markdown textarea.
    expect(await screen.findByRole("heading", { name: "Tailored Resume" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Chandra Pandey" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Professional Summary" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Core Skills" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Edit summary")).not.toBeInTheDocument();
    expect(screen.queryByText(/^#/)).not.toBeInTheDocument(); // no raw markdown
  });

  it("resume preview shows Education once and a Selected Projects section", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Resume" }));

    await screen.findByRole("heading", { name: "Tailored Resume" });
    expect(screen.getByRole("heading", { name: "Education" })).toBeInTheDocument();
    expect(screen.getAllByText(/Arizona State University/)).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Selected Projects" })).toBeInTheDocument();
    expect(screen.getByText("Luna AI")).toBeInTheDocument();
  });

  it("renders quality badges from the backend quality object", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Resume" }));

    await screen.findByRole("heading", { name: "Tailored Resume" });
    expect(screen.getByText("ATS-friendly")).toBeInTheDocument();
    expect(screen.getByText("Tailored to job")).toBeInTheDocument();
    expect(screen.getByText("Truth-checked")).toBeInTheDocument();
  });

  it("toggles to edit mode and reflects edits in preview", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Resume" }));
    await screen.findByRole("heading", { name: "Tailored Resume" });

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const summary = screen.getByLabelText("Edit summary");
    await userEvent.clear(summary);
    await userEvent.type(summary, "Edited summary line");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByText("Edited summary line")).toBeInTheDocument();
  });

  it("does not show a template-mode warning when AI generation succeeds", async () => {
    mockJobsFetch({ resumeWarnings: [] });
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Resume" }));

    await screen.findByRole("heading", { name: "Tailored Resume" });
    expect(screen.queryByText(/template mode/i)).not.toBeInTheDocument();
  });

  it("shows the specific backend reason when AI falls back to template mode", async () => {
    mockJobsFetch({
      resumeWarnings: ["Generated with template mode because the configured model was not found."]
    });
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Resume" }));

    await screen.findByRole("heading", { name: "Tailored Resume" });
    expect(
      screen.getByText("Generated with template mode because the configured model was not found.")
    ).toBeInTheDocument();
  });

  it("Cover Letter button generates and opens the cover letter modal", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Cover Letter" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "http://localhost:8000/jobs/1/generate-cover-letter",
        expect.objectContaining({ method: "POST" })
      )
    );
    // Rendered as a letter page: heading + greeting + paragraph text, no markdown.
    expect(await screen.findByRole("heading", { name: "Cover Letter" })).toBeInTheDocument();
    expect(screen.getByText("Dear Hiring Team,")).toBeInTheDocument();
    expect(screen.getByText("I am excited to apply for the ML Engineer role at Acme.")).toBeInTheDocument();
    expect(screen.getByText("Chandra Pandey")).toBeInTheDocument();
  });

  it("copies clean plain text (no markdown symbols)", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    mockJobsFetch();
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Resume" }));
    await screen.findByRole("heading", { name: "Tailored Resume" });

    await userEvent.click(screen.getByRole("button", { name: "Copy text" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).not.toContain("#");
    expect(copied).not.toContain("**");
    expect(copied).toContain("Chandra Pandey");
  });

  it("re-fetches with the selected posted-within value and updates visible jobs", async () => {
    // Server returns only a recent job for 7 days, and an extra 10-day-old job
    // for 15 days. Changing the dropdown must call the API with the new value
    // AND surface the previously-withheld job.
    const recent = makeJob({ id: 1, title: "Recent Backend Engineer", posted_at: isoDaysAgo(2) });
    const older = makeJob({ id: 2, title: "Older Backend Engineer", posted_at: isoDaysAgo(10) });
    const requestedDays: number[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = new URL(String(input), "http://localhost:8000");
      if (url.pathname === "/jobs") {
        const days = Number(url.searchParams.get("posted_within_days"));
        requestedDays.push(days);
        const jobs = days >= 15 ? [recent, older] : [recent];
        return Promise.resolve(
          jsonResponse({
            profile_complete: true,
            criteria: { role_queries: [], skills: [], seniority_targets: [] },
            profile_filters: { target_roles: [], target_levels: [], preferred_locations: [], work_preference: "remote" },
            jobs
          })
        );
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(React.createElement(JobDiscovery));

    // Initial load uses 7 days: only the recent job is visible.
    expect(await screen.findByText("Recent Backend Engineer")).toBeInTheDocument();
    expect(screen.queryByText("Older Backend Engineer")).not.toBeInTheDocument();
    expect(requestedDays).toContain(7);

    await userEvent.selectOptions(screen.getByLabelText("Posted within days"), "15");

    // The API was called with the new window and the 10-day-old job now shows.
    await waitFor(() => expect(requestedDays).toContain(15));
    expect(await screen.findByText("Older Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Recent Backend Engineer")).toBeInTheDocument();
  });

  it("shows the fit score with its range color classes for each range", async () => {
    mockJobsFetch({
      discovered: [
        makeJob({ id: 1, title: "Strong Role", match: { ...makeJob().match, fit_score: 88, fit_label: "Strong fit" } }),
        makeJob({ id: 2, title: "Good Role", match: { ...makeJob().match, fit_score: 79, fit_label: "Good fit", missing_skills: [] } }),
        makeJob({ id: 3, title: "Stretch Role", match: { ...makeJob().match, fit_score: 55, fit_label: "Stretch", missing_skills: [] } }),
        makeJob({ id: 4, title: "Low Role", match: { ...makeJob().match, fit_score: 30, fit_label: "Low fit", missing_skills: [] } })
      ]
    });
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));

    const badge = async (title: string) => {
      const card = (await screen.findByText(title)).closest("article") as HTMLElement;
      return card.querySelector("[data-fit-tone]") as HTMLElement;
    };
    const strong = await badge("Strong Role");
    expect(strong).toHaveAttribute("data-fit-tone", "strong");
    expect(strong.className).toContain("bg-emerald-50");
    const good = await badge("Good Role");
    expect(good.className).toContain("bg-lime-50"); // yellow-green, not red-leaning
    expect(good.className).not.toContain("red");
    expect((await badge("Stretch Role")).className).toContain("bg-orange-50");
    expect((await badge("Low Role")).className).toContain("bg-red-50");
    expect(within(strong).getByText("88")).toBeInTheDocument();
    // No colored top strip element inside any badge.
    expect(strong.querySelector("[class*='h-1.5']")).toBeNull();
    expect(good.querySelector("[aria-hidden]")).toBeNull();
  });

  it("shows a company logo image on the card when a logo URL is provided", async () => {
    mockJobsFetch({
      discovered: [makeJob({ company: "OpenAI", company_logo_url: "https://logo.clearbit.com/openai.com" })]
    });
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));

    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    const logo = within(card).getByRole("img", { name: "OpenAI logo" });
    expect(logo).toHaveAttribute("src", "https://logo.clearbit.com/openai.com");
  });

  it("falls back to the neutral placeholder (never the company initial) when no logo URL is available", async () => {
    mockJobsFetch({ discovered: [makeJob({ company: "Acme", company_logo_url: "" })] });
    render(React.createElement(JobDiscovery));
    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));

    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    expect(within(card).queryByRole("img")).not.toBeInTheDocument();
    expect(within(card).getByTestId("company-logo-placeholder")).toBeInTheDocument();
    expect(within(card).queryByText("A")).not.toBeInTheDocument();
  });

  it("saves a job via the official save action", async () => {
    mockJobsFetch();
    render(React.createElement(JobDiscovery));

    await userEvent.click(await screen.findByRole("button", { name: /Find fresh jobs/ }));
    const card = (await screen.findByText("Machine Learning Engineer")).closest("article") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: /Save/ }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "http://localhost:8000/jobs/1/save",
        expect.objectContaining({ method: "POST" })
      )
    );
  });
});
