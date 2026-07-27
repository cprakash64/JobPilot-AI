import { expect, test, type Page, type Route } from "@playwright/test";

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

test("job card discovery is explicit, persisted-ID scoped, cached, and shared with the modal", async ({ page }) => {
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
    application_url: "https://job-boards.greenhouse.io/acme/jobs/provider-9982",
    source_url: "https://job-boards.greenhouse.io/acme/jobs/provider-9982"
  };

  await page.addInitScript(() => localStorage.setItem("jobpilot_token", "synthetic-e2e-token"));
  await page.setViewportSize({ width: 1024, height: 640 });
  await page.context().route("https://www.linkedin.com/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "<title>Provider profile</title>" });
  });
  await page.route(`${API}/jobs**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/jobs") {
      return json(route, {
        profile_complete: true,
        criteria: { role_queries: [], skills: [], seniority_targets: [] },
        profile_filters: {
          target_roles: [],
          target_levels: [],
          preferred_locations: [],
          work_preference: "remote"
        },
        jobs: [job, untouchedJob]
      });
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
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/jobs");
  const card = page.getByRole("article").filter({ hasText: "Machine Learning Engineer" });
  const untouchedCard = page.getByRole("article").filter({ hasText: "Data Platform Engineer" });
  await expect(card.getByRole("heading", { name: "People Who Can Help" })).toBeVisible();
  await expect(untouchedCard.getByRole("heading", { name: "People Who Can Help" })).toBeVisible();
  expect(peopleRequests).toEqual([]);

  await card.getByRole("button", { name: "Find people" }).click();
  await expect(card.getByText("Rita Recruiter")).toBeVisible();
  await expect(card.getByText("Pat Referrer")).toBeVisible();
  await expect(card.getByText("1 recruiter · 1 potential manager · 1 referral candidate")).toBeVisible();
  expect(peopleRequests.slice(0, 2)).toEqual([
    `GET /jobs/${persistedJobId}/people`,
    `POST /jobs/${persistedJobId}/people/discover`
  ]);
  expect(peopleRequests.some((request) => request.includes("/jobs/732/people"))).toBe(false);

  const profile = card.getByRole("link", { name: "LinkedIn profile" });
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
  await card.getByRole("button", { name: "Find work email" }).first().click();
  await expect(card.getByText("Verified work email: rita@acme.example")).toBeVisible();
  expect(emailCount).toBe(1);

  await card.getByRole("button", { name: "View all people" }).click();
  const dialog = page.getByRole("dialog", { name: "Machine Learning Engineer" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close details" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: /Apply on official site/ })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "People Who Can Help" })).toBeVisible();
  await expect(dialog.getByText("Rita Recruiter")).toBeVisible();
  await expect(dialog.getByText("Morgan Manager")).toBeVisible();
  await expect(dialog.getByText("Pat Referrer")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Likely Recruiters" })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Potential Hiring Managers" })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Potential Referral Candidates" })).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox).not.toBeNull();
  expect(dialogBox!.y).toBeGreaterThanOrEqual(0);
  expect(dialogBox!.y + dialogBox!.height).toBeLessThanOrEqual(640);

  await dialog.getByRole("button", { name: "Close details" }).click();
  await card.getByRole("button", { name: "View details" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Rita Recruiter")).toBeVisible();
  expect(discoveryCount).toBe(1);
  expect(emailCount).toBe(1);
  expect(peopleRequests.some((request) => request.includes("/jobs/732/people"))).toBe(false);
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

  await page.addInitScript(() => localStorage.setItem("jobpilot_token", "synthetic-e2e-token"));
  await page.route(`${API}/jobs/7/people**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    expect(request.headers().authorization).toBe("Bearer synthetic-e2e-token");

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
        draft: "Hi Rita,\n\nI’m applying for the Machine Learning Engineer role at Acme AI.",
        requires_user_review: true,
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
    return route.fulfill({ status: 404, body: "{}" });
  });

  await page.goto("/jobs/7");
  await expect(page.getByText(/Find recruiters and referral candidates/)).toBeVisible();
  await page.getByRole("button", { name: "Find people" }).click();
  await expect(page.getByText("Rita Recruiter")).toBeVisible();
  await expect(page.getByText("No potential manager met JobPilot’s confidence threshold.")).toBeVisible();
  await expect(page.getByText("No relevant employee met JobPilot’s referral threshold.")).toBeVisible();

  await page.getByRole("button", { name: /Find work email/ }).click();
  await expect(page.getByText(/Verified work email: rita@acme.example/)).toBeVisible();

  await page.getByRole("button", { name: /Draft outreach/ }).click();
  const dialog = page.getByRole("dialog", { name: "Review outreach draft" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/never sends this message automatically/i)).toBeVisible();
  await dialog.getByRole("button", { name: "Close outreach draft" }).click();

  await page.getByRole("button", { name: /Save contact/ }).click();
  await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();
  await page.getByRole("button", { name: /Mark contacted/ }).click();
  await expect(page.getByRole("button", { name: "Contacted" })).toBeDisabled();

  await page.reload();
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
      return json(route, {
        profile_complete: true,
        criteria: { role_queries: [], skills: [], seniority_targets: [] },
        profile_filters: {
          target_roles: [],
          target_levels: [],
          preferred_locations: [],
          work_preference: "hybrid"
        },
        jobs: [toshibaJob]
      });
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
  const card = page.getByRole("article").filter({ hasText: "AI Engineer Intern" });
  await expect(card.getByRole("img", { name: "Toshiba Global Commerce logo" })).toHaveAttribute(
    "src",
    "https://commerce.toshiba.com/images/tgcs/logo.png"
  );
  expect(exactSearches).toBe(0);

  await card.getByRole("button", { name: "Find people" }).click();
  await expect(card.getByText("No reliable recruiting contact met JobPilot’s threshold.")).toBeVisible();
  expect(exactSearches).toBe(1);

  await card.getByRole("button", { name: "Broaden search" }).dblclick();
  await expect(card.getByText("Rita Recruiter")).toBeVisible();
  expect(broadenedSearches).toBe(1);

  await page.reload();
  await card.getByRole("button", { name: "View details" }).click();
  await expect(
    page.getByRole("dialog", { name: "AI Engineer Intern" }).getByText("Rita Recruiter")
  ).toBeVisible();
  expect(exactSearches).toBe(1);
  expect(broadenedSearches).toBe(1);
  expect(await card.getByRole("button", { name: "Find work email" }).count()).toBe(0);
});
