import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The Jobs master-detail workspace, end to end against a mocked API.
 *
 * Everything the redesign promises is checked as one flow: the whole card opens
 * the job, the sidebar becomes a rail, the compact list stays, the detail panel
 * owns the workspace, and no people search happens until it is asked for.
 */

const API = process.env.API_BASE_URL ?? "http://localhost:8000";

const recruiter = {
  recommendation_id: 41,
  full_name: "Rita Recruiter",
  current_title: "Senior Technical Recruiter",
  current_company: "Northwind",
  category: "likely_recruiter",
  category_label: "Likely recruiter",
  relevance_score: 88,
  confidence: "high",
  current_employment_confidence: 0.95,
  employment_validation_status: "confirmed_exact_company_verified",
  employment_last_verified_at: "2026-07-25T12:00:00Z",
  employment_warning: null,
  email_lookup_allowed: true,
  reasons: ["Currently listed by a professional data source at the hiring company."],
  limitations: ["Recruiting responsibility for this specific opening has not been confirmed."],
  last_checked_at: "2026-07-25T12:00:00Z",
  professional_profile_url: "https://www.linkedin.com/in/rita-recruiter",
  email_status: "not_requested",
  professional_email: null,
  email_verified_at: null,
  saved: false,
  contacted: false
};

const manager = {
  ...recruiter,
  recommendation_id: 42,
  full_name: "Morgan Manager",
  current_title: "Director of Data Platform",
  category: "potential_hiring_manager",
  category_label: "Potential hiring manager",
  professional_profile_url: null
};

function daysAgo(days: number) {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

function job(id: number, title: string, company: string, days: number, extra: Record<string, unknown> = {}) {
  return {
    id,
    title,
    company,
    company_domain: "",
    company_logo_url: "",
    source: "greenhouse",
    location: "Remote, United States",
    location_display: "Remote",
    workplace_type: "remote",
    employment_type: "full-time",
    seniority_level: "mid",
    posted_at: daysAgo(days),
    discovered_at: daysAgo(days),
    application_url: `https://job-boards.greenhouse.io/acme/jobs/${id}`,
    source_url: `https://job-boards.greenhouse.io/acme/jobs/${id}`,
    description_clean: "Build reliable data systems.\n\n- Ship services\n- Own reliability",
    required_skills: ["Python", "PyTorch", "Kubernetes"],
    preferred_skills: [],
    responsibilities: ["Ship services"],
    salary_min: 150000,
    salary_max: 190000,
    salary_currency: "USD",
    match: {
      fit_score: 88,
      fit_label: "Strong fit",
      score_state: "scored",
      match_reasons: ["Your production Python experience aligns with this role."],
      missing_skills: ["Kubernetes"],
      risk_factors: [],
      recommended_resume_angle: "Lead with production systems.",
      confidence: 0.9,
      explanation_source: "deterministic",
      fit_summary: "Strong overlap with your production Python work."
    },
    ...extra
  };
}

const JOBS = [
  job(1, "Machine Learning Engineer", "Acme AI", 1),
  job(2, "Data Platform Engineer", "Northwind", 2, { salary_min: null, salary_max: null }),
  job(3, "Backend Engineer", "Contoso", 3),
  job(4, "Research Engineer", "Globex", 4),
  job(5, "Platform Engineer", "Initech", 5)
];

async function json(route: Route, body: object) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

const notStartedPeople = {
  status: "not_started",
  availability_reason: "available",
  beta: true,
  quota: { daily_limit: 20, daily_used: 8, daily_remaining: 12, resets_at: "2026-07-30T00:00:00.000Z" },
  categories: { likely_recruiters: [], potential_hiring_managers: [], potential_referrers: [] },
  warnings: [],
  controls: { email_discovery: true, outreach_drafting: true }
};

async function installApi(page: Page) {
  const peopleRequests: string[] = [];
  const discovered = new Set<number>();

  await page.addInitScript(() => localStorage.setItem("jobpilot_token", "synthetic-e2e-token"));
  await page.route(`${API}/jobs**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (method === "GET" && path === "/jobs") {
      return json(route, {
        profile_complete: true,
        criteria: { role_queries: [], skills: [], seniority_targets: [] },
        profile_filters: {
          target_roles: [],
          target_levels: [],
          preferred_locations: [],
          work_preference: "remote"
        },
        jobs: JOBS
      });
    }
    if (path.startsWith("/jobs/tracker/")) {
      return json(route, { applications: [] });
    }
    const people = path.match(/^\/jobs\/(\d+)\/people$/);
    if (people) {
      peopleRequests.push(`${method} ${path}`);
      const id = Number(people[1]);
      return json(
        route,
        discovered.has(id)
          ? {
              ...notStartedPeople,
              status: "complete",
              categories: {
                likely_recruiters: [recruiter],
                potential_hiring_managers: [manager],
                potential_referrers: []
              },
              quota: { ...notStartedPeople.quota, daily_used: 9, daily_remaining: 11 }
            }
          : notStartedPeople
      );
    }
    const discover = path.match(/^\/jobs\/(\d+)\/people\/discover$/);
    if (discover) {
      peopleRequests.push(`${method} ${path}`);
      discovered.add(Number(discover[1]));
      return json(route, {
        ...notStartedPeople,
        status: "complete",
        categories: {
          likely_recruiters: [recruiter],
          potential_hiring_managers: [manager],
          potential_referrers: []
        },
        quota: { ...notStartedPeople.quota, daily_used: 9, daily_remaining: 11 }
      });
    }
    if (path.endsWith("/generate-resume") || path.endsWith("/generate-cover-letter")) {
      const isResume = path.endsWith("/generate-resume");
      return json(route, {
        document_id: isResume ? 7 : 8,
        document_type: isResume ? "resume" : "cover_letter",
        title: isResume ? "Tailored Resume - Acme" : "Cover Letter - Acme",
        content: isResume
          ? {
              header: { full_name: "Chandra Pandey", email: "", phone: "", location: "", links: [] },
              summary: "Backend engineer with production Python experience.",
              skills: [],
              experience: [],
              projects: [],
              education: [],
              awards: []
            }
          : {
              date: "",
              recipient: "Hiring Team",
              company: "Acme",
              role: "Engineer",
              greeting: "Dear Hiring Team,",
              paragraphs: ["I am excited to apply."],
              closing: "Best regards,",
              signature: "Chandra Pandey"
            },
        markdown: "",
        plain_text: "",
        quality: { warnings: [] },
        warnings: [],
        unsupported_claims_removed: [],
        download_urls: { docx: null, pdf: null }
      });
    }
    if (path.endsWith("/save")) {
      return json(route, { tracker: { id: 1, job_id: 1, status: "saved" } });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  return { peopleRequests };
}

test("the whole card opens a master-detail workspace with manual networking", async ({ page }) => {
  const { peopleRequests } = await installApi(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.context().route("https://www.linkedin.com/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "<title>Provider profile</title>" });
  });

  await page.goto("/jobs");

  // 1. The list state: full sidebar, no job selected, five clean cards.
  const sidebar = page.getByRole("complementary", { name: "Primary" });
  await expect(page.getByRole("heading", { level: 1, name: "Jobs" })).toBeVisible();
  await expect(sidebar).toHaveAttribute("data-collapsed", "false");
  await expect(page.getByTestId("job-card")).toHaveCount(5);
  await expect(page.getByRole("tablist", { name: "Job sections" })).toHaveCount(0);
  // No expanded People block on any card, and no people request at all.
  await expect(page.getByRole("heading", { name: "People Who Can Help" })).toHaveCount(0);
  expect(peopleRequests).toEqual([]);

  // 2. Clicking the body of the second card opens it.
  const secondCard = page.getByTestId("job-card").nth(1);
  await expect(secondCard).toContainText("Data Platform Engineer");
  await secondCard.click({ position: { x: 200, y: 12 } });

  await expect(page).toHaveURL(/\?job=2$/);
  await expect(sidebar).toHaveAttribute("data-collapsed", "true");
  await expect(page.getByTestId("compact-job-card")).toHaveCount(5);
  await expect(page.getByTestId("compact-job-card").nth(1)).toHaveAttribute("aria-current", "true");

  // 3. The detail panel: identity, fit, and truthful salary handling.
  const detail = page.getByRole("heading", { level: 1, name: "Data Platform Engineer" });
  await expect(detail).toBeVisible();
  await expect(page.getByText("Northwind").first()).toBeVisible();
  await expect(page.getByText("88").first()).toBeVisible();
  await expect(page.getByText("Strong overlap with your production Python work.")).toBeVisible();
  // This job has no published range, so its header shows no salary at all —
  // while other jobs in the list keep theirs.
  const detailHeader = page
    .locator("header")
    .filter({ has: page.getByRole("heading", { level: 1, name: "Data Platform Engineer" }) });
  await expect(detailHeader.getByText(/\$\d+k/)).toHaveCount(0);
  await expect(page.getByTestId("compact-job-card").first().getByText(/\$\d+k/)).toBeVisible();

  // 4. Job description.
  await page.getByRole("tab", { name: "Job description" }).click();
  await expect(page.getByText("Build reliable data systems.")).toBeVisible();
  await expect(page.getByRole("link", { name: /Open original posting/ })).toHaveAttribute(
    "href",
    "https://job-boards.greenhouse.io/acme/jobs/2"
  );

  // 5. Networking reads stored state only; nothing paid runs unasked.
  await page.getByRole("tab", { name: "Networking" }).click();
  await expect(page.getByRole("heading", { name: "People Who Can Help" })).toBeVisible();
  await expect(page.getByText("12 of 20 people searches remaining today.")).toBeVisible();
  expect(peopleRequests).toEqual(["GET /jobs/2/people"]);

  await page.getByRole("button", { name: "Find people" }).click();
  await expect(page.getByText("Rita Recruiter")).toBeVisible();
  await expect(page.getByText("Morgan Manager")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Likely Recruiters" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Potential Hiring Managers" })).toBeVisible();
  await expect(page.getByText(/not confirmed|has not been confirmed/i).first()).toBeVisible();
  // Bookkeeping actions are gone; the two channels are what remain.
  await expect(page.getByRole("button", { name: /Save contact/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Mark contacted/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /LinkedIn/ }).first()).toBeVisible();
  expect(peopleRequests.filter((entry) => entry.startsWith("POST"))).toEqual([
    "POST /jobs/2/people/discover"
  ]);

  // 6. Both documents from one Application materials section.
  await page.getByRole("tab", { name: "Application materials" }).click();
  await expect(page.getByRole("heading", { name: "Tailored resume" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cover letter" })).toBeVisible();
  await expect(page.getByText("Not generated yet")).toHaveCount(2);

  await page.getByRole("button", { name: "Generate tailored resume" }).click();
  // The generated document opens in its own preview dialog.
  await expect(page.getByRole("button", { name: "Close document" })).toBeVisible();
  await expect(page.getByText("Backend engineer with production Python experience.")).toBeVisible();
  await page.getByRole("button", { name: "Close document" }).click();
  await expect(page.getByText("Ready · Tailored Resume - Acme")).toBeVisible();
  await expect(page.getByRole("button", { name: /Preview and download/ })).toBeVisible();

  await page.getByRole("button", { name: "Generate cover letter" }).click();
  await expect(page.getByRole("button", { name: "Close document" })).toBeVisible();
  await expect(page.getByText("I am excited to apply.")).toBeVisible();
  await page.getByRole("button", { name: "Close document" }).click();

  // 7. Switching jobs from the compact list replaces the detail content.
  await page.getByTestId("compact-job-card").nth(3).click();
  await expect(page).toHaveURL(/\?job=4$/);
  await expect(page.getByRole("heading", { level: 1, name: "Research Engineer" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Data Platform Engineer" })).toHaveCount(0);
  // Exactly two scroll regions on desktop: the list and the detail panel.
  const scrollers = await page.evaluate(() =>
    Array.from(document.querySelectorAll("*")).filter((node) => {
      const style = getComputedStyle(node);
      return (
        (style.overflowY === "auto" || style.overflowY === "scroll") &&
        node.scrollHeight > node.clientHeight
      );
    }).length
  );
  expect(scrollers).toBeLessThanOrEqual(2);
  // …and the page behind them shows no scrollbar of its own.
  const pageScrollbar = await page.evaluate(
    () => window.innerWidth - document.documentElement.clientWidth
  );
  expect(pageScrollbar).toBe(0);
  // The new job starts on Overview, with no people request of its own.
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  expect(peopleRequests.filter((entry) => entry.includes("/jobs/4/"))).toEqual([]);

  // 8. Browser Back returns the previously open job.
  await page.goBack();
  await expect(page).toHaveURL(/\?job=2$/);
  await expect(page.getByRole("heading", { level: 1, name: "Data Platform Engineer" })).toBeVisible();

  // 9. Closing returns the list state and the full sidebar.
  await page.getByRole("button", { name: "Close job details" }).click();
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(page.getByRole("heading", { level: 1, name: "Jobs" })).toBeVisible();
  await expect(sidebar).toHaveAttribute("data-collapsed", "false");
  await expect(page.getByTestId("job-card")).toHaveCount(5);
  // The card now reports the people it already has, without a new request.
  const requestsBefore = peopleRequests.length;
  await expect(page.getByTestId("job-card").nth(1).getByRole("button", { name: "2 people" })).toBeVisible();
  expect(peopleRequests).toHaveLength(requestsBefore);
});

test("a copied workspace URL opens the same job, and mobile shows a full-width detail", async ({ page }) => {
  const { peopleRequests } = await installApi(page);

  // Deep link straight to a job, as a shared link would.
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/jobs?job=3&q=engineer");
  await expect(page.getByRole("heading", { level: 1, name: "Backend Engineer" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Primary" })).toHaveAttribute(
    "data-collapsed",
    "true"
  );
  // Reloading keeps the same job open.
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Backend Engineer" })).toBeVisible();

  // Escape closes the detail view but keeps the filter.
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/q=engineer/);
  await expect(page).not.toHaveURL(/job=3/);
  await expect(page.getByLabel("Role or skill").first()).toHaveValue("engineer");

  // Mobile: the list first, then a full-width detail with a way back.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/jobs");
  await expect(page.getByTestId("job-card")).toHaveCount(5);
  await page.getByTestId("job-card").first().click({ position: { x: 40, y: 12 } });
  await expect(page.getByRole("heading", { level: 1, name: "Machine Learning Engineer" })).toBeVisible();
  // The compact column is hidden on a phone; the detail owns the viewport.
  await expect(page.getByTestId("compact-job-card").first()).toBeHidden();
  const back = page.getByRole("button", { name: /Back to jobs/ }).first();
  await expect(back).toBeVisible();
  await back.click();
  await expect(page.getByRole("heading", { level: 1, name: "Jobs" })).toBeVisible();
  expect(peopleRequests).toEqual([]);
});

test("the whole list scales without per-card detail or people traffic", async ({ page }) => {
  const many = Array.from({ length: 60 }, (_value, index) =>
    job(100 + index, `Role ${index}`, `Company ${index}`, (index % 6) + 1)
  );
  const requests: string[] = [];
  await page.addInitScript(() => localStorage.setItem("jobpilot_token", "synthetic-e2e-token"));
  await page.route(`${API}/jobs**`, async (route) => {
    const url = new URL(route.request().url());
    requests.push(`${route.request().method()} ${url.pathname}`);
    if (url.pathname === "/jobs") {
      return json(route, {
        profile_complete: true,
        criteria: { role_queries: [], skills: [], seniority_targets: [] },
        profile_filters: { target_roles: [], target_levels: [], preferred_locations: [], work_preference: "remote" },
        jobs: many
      });
    }
    if (url.pathname.startsWith("/jobs/tracker/")) {
      return json(route, { applications: [] });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/jobs");
  await expect(page.getByTestId("job-card")).toHaveCount(60);

  expect(requests.filter((entry) => entry.includes("/people"))).toEqual([]);
  expect(requests.filter((entry) => /^GET \/jobs\/\d+$/.test(entry))).toEqual([]);
  expect(requests.sort()).toEqual(["GET /jobs", "GET /jobs/tracker/all"]);
});
