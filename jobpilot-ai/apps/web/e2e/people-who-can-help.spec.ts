import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * People Who Can Help, end to end against a mocked provider.
 *
 * The panel now lives in the Jobs workspace's Networking section rather than
 * inside every job card. What must not change: rendering a list never touches
 * the people API, every search is one explicit click, results are cached and
 * scoped to the persisted job id, and nothing is ever sent on the user's behalf.
 */

const API = process.env.API_BASE_URL ?? "http://localhost:8000";

const basePerson = {
  recommendation_id: 41,
  full_name: "Rita Recruiter",
  current_title: "Senior Technical Recruiter",
  current_company: "Acme AI",
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
  email_status: "not_requested" as string,
  professional_email: null as string | null,
  email_verified_at: null as string | null,
  saved: false,
  contacted: false
};

function payload(person: typeof basePerson | null, status = "complete") {
  return {
    status,
    availability_reason: "available",
    beta: true,
    categories: {
      likely_recruiters: person ? [person] : [],
      potential_hiring_managers: [],
      potential_referrers: []
    },
    warnings: status === "no_reliable_matches" ? ["No sufficiently reliable people were found."] : [],
    controls: { email_discovery: true, outreach_drafting: true }
  };
}

async function json(route: Route, body: object) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

function jobsPayload(jobs: unknown[]) {
  return {
    profile_complete: true,
    criteria: { role_queries: [], skills: [], seniority_targets: [] },
    profile_filters: {
      target_roles: [],
      target_levels: [],
      preferred_locations: [],
      work_preference: "remote"
    },
    jobs
  };
}

/** Opens a job by clicking its card body, then activates Networking. */
async function openNetworking(page: Page, title: string) {
  await page.getByTestId("job-card").filter({ hasText: title }).first().click({ position: { x: 60, y: 12 } });
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
  await page.getByRole("tab", { name: "Networking" }).click();
}

test("people discovery is explicit, persisted-ID scoped, and cached across the workspace", async ({ page }) => {
  const persistedJobId = 731;
  let discovered = false;
  let discoveryCount = 0;
  let emailCount = 0;
  const peopleRequests: string[] = [];
  let recruiter = { ...basePerson };
  const manager = {
    ...recruiter,
    recommendation_id: 42,
    full_name: "Morgan Manager",
    current_title: "Director of Machine Learning",
    category: "potential_hiring_manager",
    category_label: "Potential hiring manager",
    professional_profile_url: null
  };
  const referrer = {
    ...recruiter,
    recommendation_id: 43,
    full_name: "Pat Referrer",
    current_title: "Machine Learning Engineer",
    category: "potential_referrer",
    category_label: "Potential referral candidate",
    professional_profile_url: null
  };
  const job = {
    id: persistedJobId,
    title: "Machine Learning Engineer",
    company: "Acme AI",
    company_domain: "acme.example",
    company_logo_url: "",
    source: "greenhouse",
    location: "Remote",
    workplace_type: "remote",
    employment_type: "full-time",
    seniority_level: "mid",
    posted_at: "2026-07-25T12:00:00Z",
    discovered_at: "2026-07-25T12:00:00Z",
    application_url: "https://job-boards.greenhouse.io/acme/jobs/provider-9981",
    source_url: "https://job-boards.greenhouse.io/acme/jobs/provider-9981",
    description_clean: "Build production machine-learning systems.",
    required_skills: ["Python", "PyTorch"],
    preferred_skills: [],
    responsibilities: ["Ship reliable models."],
    match: {
      fit_score: 92,
      fit_label: "Strong fit",
      score_state: "scored",
      match_reasons: ["Your production Python experience aligns with this role."],
      missing_skills: [],
      risk_factors: [],
      recommended_resume_angle: "Lead with production ML systems.",
      confidence: 0.9,
      explanation_source: "deterministic"
    }
  };
  const untouchedJob = {
    ...job,
    id: 732,
    title: "Data Platform Engineer",
    posted_at: "2026-07-24T12:00:00Z",
    application_url: "https://job-boards.greenhouse.io/acme/jobs/provider-9982",
    source_url: "https://job-boards.greenhouse.io/acme/jobs/provider-9982"
  };

  await page.addInitScript(() => localStorage.setItem("jobpilot_token", "synthetic-e2e-token"));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.context().route("https://www.linkedin.com/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "<title>Provider profile</title>" });
  });
  await page.route(`${API}/jobs**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/jobs") {
      return json(route, jobsPayload([job, untouchedJob]));
    }
    if (url.pathname.startsWith("/jobs/tracker/")) {
      return json(route, { applications: [] });
    }
    if (url.pathname === `/jobs/${persistedJobId}/people`) {
      peopleRequests.push(`${request.method()} ${url.pathname}`);
      return json(
        route,
        discovered
          ? {
              ...payload(recruiter),
              categories: {
                likely_recruiters: [recruiter],
                potential_hiring_managers: [manager],
                potential_referrers: [referrer]
              }
            }
          : payload(null, "not_started")
      );
    }
    if (url.pathname === `/jobs/${persistedJobId}/people/discover`) {
      peopleRequests.push(`${request.method()} ${url.pathname}`);
      discoveryCount += 1;
      discovered = true;
      return json(route, {
        ...payload(recruiter),
        categories: {
          likely_recruiters: [recruiter],
          potential_hiring_managers: [manager],
          potential_referrers: [referrer]
        }
      });
    }
    if (url.pathname === `/jobs/${persistedJobId}/people/${recruiter.recommendation_id}/email`) {
      peopleRequests.push(`${request.method()} ${url.pathname}`);
      emailCount += 1;
      recruiter = {
        ...recruiter,
        email_status: "verified",
        professional_email: "rita@acme.example",
        email_verified_at: "2026-07-25T12:05:00Z"
      };
      return json(route, {
        status: "verified",
        professional_email: recruiter.professional_email,
        verified_at: recruiter.email_verified_at
      });
    }
    if (url.pathname.match(/^\/jobs\/\d+\/people/)) {
      peopleRequests.push(`${request.method()} ${url.pathname}`);
      return json(route, payload(null, "not_started"));
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/jobs");

  // The list holds a compact People action per card and no results block.
  const cards = page.getByTestId("job-card");
  await expect(cards).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "People Who Can Help" })).toHaveCount(0);
  await expect(cards.first().getByRole("button", { name: "Find people" })).toBeVisible();
  expect(peopleRequests).toEqual([]);

  await openNetworking(page, "Machine Learning Engineer");
  // Opening the section reads stored state for the persisted job id only. The
  // control appears once that read lands, so waiting for it also proves the
  // read happened — and that nothing else did.
  await expect(page.getByRole("heading", { name: "People Who Can Help" })).toBeVisible();
  const findPeople = page.getByRole("button", { name: "Find people" });
  await expect(findPeople).toBeVisible();
  expect(peopleRequests).toEqual([`GET /jobs/${persistedJobId}/people`]);

  await findPeople.click();
  await expect(page.getByText("Rita Recruiter")).toBeVisible();
  await expect(page.getByText("Morgan Manager")).toBeVisible();
  await expect(page.getByText("Pat Referrer")).toBeVisible();
  await expect(page.getByText("1 recruiter · 1 potential manager · 1 referral candidate")).toBeVisible();
  expect(peopleRequests.slice(0, 2)).toEqual([
    `GET /jobs/${persistedJobId}/people`,
    `POST /jobs/${persistedJobId}/people/discover`
  ]);
  expect(peopleRequests.some((request) => request.includes("/jobs/732/people"))).toBe(false);

  const profile = page.getByRole("link", { name: "LinkedIn profile" }).first();
  await expect(profile).toHaveAttribute("href", "https://www.linkedin.com/in/rita-recruiter");
  await expect(profile).toHaveAttribute("target", "_blank");
  await expect(profile).toHaveAttribute("rel", "noopener noreferrer");
  const popupPromise = page.waitForEvent("popup");
  await profile.click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  expect(popup.url()).toContain("linkedin.com/in/rita-recruiter");
  await popup.close();

  expect(emailCount).toBe(0);
  await page.getByRole("button", { name: "Find work email" }).first().click();
  await expect(page.getByText("Verified work email: rita@acme.example")).toBeVisible();
  expect(emailCount).toBe(1);

  // Switching to the other job in the compact list starts nothing for it.
  await page.getByTestId("compact-job-card").filter({ hasText: "Data Platform Engineer" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Data Platform Engineer" })).toBeVisible();
  expect(peopleRequests.some((request) => request.includes("/jobs/732/people"))).toBe(false);

  // Returning to the first job re-uses the cached search: still one discovery.
  await page.getByTestId("compact-job-card").filter({ hasText: "Machine Learning Engineer" }).click();
  await page.getByRole("tab", { name: "Networking" }).click();
  await expect(page.getByText("Rita Recruiter")).toBeVisible();
  expect(discoveryCount).toBe(1);
  expect(emailCount).toBe(1);
});

test("complete people workflow remains manual, grounded, cached, and user-scoped", async ({ page }) => {
  let discovered = false;
  let person = {
    ...basePerson,
    email_status: "not_requested" as string,
    professional_email: null as string | null,
    email_verified_at: null as string | null
  };
  let suppressed = false;
  let getCount = 0;
  const job = {
    id: 7,
    title: "Machine Learning Engineer",
    company: "Acme AI",
    company_domain: "acme.example",
    company_logo_url: "",
    source: "greenhouse",
    location: "Remote",
    workplace_type: "remote",
    employment_type: "full-time",
    seniority_level: "mid",
    posted_at: "2026-07-25T12:00:00Z",
    discovered_at: "2026-07-25T12:00:00Z",
    application_url: "https://job-boards.greenhouse.io/acme/jobs/7",
    source_url: "https://job-boards.greenhouse.io/acme/jobs/7",
    description_clean: "Build production machine-learning systems.",
    required_skills: ["Python"],
    preferred_skills: [],
    responsibilities: ["Ship reliable models."],
    match: null
  };

  await page.addInitScript(() => localStorage.setItem("jobpilot_token", "synthetic-e2e-token"));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route(`${API}/jobs**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    expect(request.headers().authorization).toBe("Bearer synthetic-e2e-token");

    if (request.method() === "GET" && path === "/jobs") {
      return json(route, jobsPayload([job]));
    }
    if (path.startsWith("/jobs/tracker/")) {
      return json(route, { applications: [] });
    }
    if (request.method() === "GET" && path === "/jobs/7/people") {
      getCount += 1;
      return json(route, discovered ? payload(suppressed ? null : person) : payload(null, "not_started"));
    }
    if (path.endsWith("/discover")) {
      discovered = true;
      return json(route, payload(person));
    }
    if (path.endsWith("/email")) {
      person = {
        ...person,
        email_status: "verified",
        professional_email: "rita@acme.example",
        email_verified_at: "2026-07-25T12:05:00Z"
      };
      return json(route, {
        status: "verified",
        professional_email: person.professional_email,
        verified_at: person.email_verified_at
      });
    }
    if (path.endsWith("/outreach-draft")) {
      return json(route, {
        message_type: "linkedin_message",
        subject: null,
        body: "Hi Rita,\n\nI’m applying for the Machine Learning Engineer role at Acme AI.",
        facts_used: ["job:Machine Learning Engineer", "company:Acme AI"],
        assumptions: [],
        omitted_uncertain_facts: ["recruiter_assignment_unconfirmed"],
        character_count: 75,
        requires_manual_review: true,
        sent: false
      });
    }
    if (path.endsWith("/save")) {
      person = { ...person, saved: request.method() === "POST" };
      return json(route, { saved: person.saved });
    }
    if (path.endsWith("/contacted")) {
      person = { ...person, contacted: true };
      return json(route, { contacted: true });
    }
    if (path.endsWith("/feedback")) {
      suppressed = true;
      return json(route, { accepted: true, suppressed: true });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  // The old per-job permalink still resolves — into the workspace.
  await page.goto("/jobs/7");
  await expect(page).toHaveURL(/\/jobs\?job=7$/);
  await page.getByRole("tab", { name: "Networking" }).click();

  await expect(page.getByText(/Find recruiters and referral candidates/)).toBeVisible();
  await page.getByRole("button", { name: "Find people" }).click();
  await expect(page.getByText("Rita Recruiter")).toBeVisible();
  await expect(page.getByText("No potential manager met JobPilot’s confidence threshold.")).toBeVisible();
  await expect(page.getByText("No relevant employee met JobPilot’s referral threshold.")).toBeVisible();

  await page.getByRole("button", { name: /Find work email/ }).click();
  await expect(page.getByText(/Verified work email: rita@acme.example/)).toBeVisible();

  await page.getByRole("button", { name: /Create LinkedIn draft/ }).click();
  const dialog = page.getByRole("dialog", { name: "Review outreach draft" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/never sends this message automatically/i)).toBeVisible();
  await dialog.getByRole("button", { name: "Close outreach draft" }).click();

  await page.getByRole("button", { name: /Save contact/ }).click();
  await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();
  await page.getByRole("button", { name: /Mark contacted/ }).click();
  await expect(page.getByRole("button", { name: "Contacted" })).toBeDisabled();

  // A reload restores the same job and its saved contact state.
  await page.reload();
  await page.getByRole("tab", { name: "Networking" }).click();
  await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();
  expect(getCount).toBeGreaterThanOrEqual(2);

  await page.getByRole("button", { name: "Report incorrect information" }).click();
  await expect(page.getByText("Rita Recruiter")).toHaveCount(0);

  // Private recommendation state is always requested with this browser user's token.
  expect(getCount).toBeGreaterThanOrEqual(3);
});

test("Toshiba no-result state broadens once and keeps its canonical logo", async ({ page }) => {
  const jobId = 7606;
  let exactSearches = 0;
  let broadenedSearches = 0;
  let state: "not_started" | "no_reliable_matches" | "complete" = "not_started";
  const toshibaJob = {
    id: jobId,
    title: "AI Engineer Intern",
    company: "Toshiba Global Commerce",
    company_domain: "commerce.toshiba.com",
    company_logo_url: "https://commerce.toshiba.com/images/tgcs/logo.png",
    source: "simplifyjobs",
    location: "Durham, NC",
    workplace_type: "hybrid",
    employment_type: "internship",
    seniority_level: "intern",
    posted_at: "2026-07-25T12:00:00Z",
    discovered_at: "2026-07-25T12:00:00Z",
    application_url: "https://job-boards.greenhouse.io/toshiba/jobs/7606",
    source_url: "https://job-boards.greenhouse.io/toshiba/jobs/7606",
    description_clean: "Build AI-enabled commerce software.",
    required_skills: ["Python"],
    preferred_skills: [],
    responsibilities: ["Develop reliable AI services."],
    match: null
  };
  const peoplePayload = () => ({
    ...payload(state === "complete" ? basePerson : null, state),
    warnings: [],
    controls: { email_discovery: false, outreach_drafting: true },
    search_scope: {
      company_scope: "Hiring company only",
      location_filter: "soft",
      parent_company_matches_included: false,
      exact_company_search_completed: state !== "not_started",
      related_company_search_attempted: state === "complete",
      broaden_eligible: state === "no_reliable_matches",
      broaden_attempted: state === "complete",
      refresh_eligible: false
    }
  });

  await page.addInitScript(() => localStorage.setItem("jobpilot_token", "synthetic-e2e-token"));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route("https://commerce.toshiba.com/images/tgcs/logo.png", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZSPcAAAAASUVORK5CYII=",
        "base64"
      )
    });
  });
  await page.route(`${API}/jobs**`, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path === "/jobs") {
      return json(route, jobsPayload([toshibaJob]));
    }
    if (path.startsWith("/jobs/tracker/")) {
      return json(route, { applications: [] });
    }
    if (request.method() === "GET" && path === `/jobs/${jobId}/people`) {
      return json(route, peoplePayload());
    }
    if (request.method() === "POST" && path === `/jobs/${jobId}/people/discover`) {
      exactSearches += 1;
      state = "no_reliable_matches";
      return json(route, peoplePayload());
    }
    if (request.method() === "POST" && path === `/jobs/${jobId}/people/broaden`) {
      broadenedSearches += 1;
      state = "complete";
      return json(route, peoplePayload());
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/jobs");
  const card = page.getByTestId("job-card").filter({ hasText: "AI Engineer Intern" });
  await expect(card.getByRole("img", { name: "Toshiba Global Commerce logo" })).toHaveAttribute(
    "src",
    "https://commerce.toshiba.com/images/tgcs/logo.png"
  );
  expect(exactSearches).toBe(0);

  await openNetworking(page, "AI Engineer Intern");
  await page.getByRole("button", { name: "Find people" }).click();
  await expect(page.getByText("No reliable recruiting contact met JobPilot’s threshold.")).toBeVisible();
  expect(exactSearches).toBe(1);

  // A double-click must still buy exactly one broadened search.
  await page.getByRole("button", { name: "Broaden search" }).dblclick();
  await expect(page.getByText("Rita Recruiter")).toBeVisible();
  expect(broadenedSearches).toBe(1);

  // The canonical logo survives into the detail header, and reloading the
  // workspace URL keeps the cached results without a new search.
  await expect(
    page.getByRole("img", { name: "Toshiba Global Commerce logo" }).first()
  ).toBeVisible();
  await page.reload();
  await page.getByRole("tab", { name: "Networking" }).click();
  await expect(page.getByText("Rita Recruiter")).toBeVisible();
  expect(exactSearches).toBe(1);
  expect(broadenedSearches).toBe(1);
  expect(await page.getByRole("button", { name: "Find work email" }).count()).toBe(0);
});
