import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PeopleWhoCanHelp } from "../components/PeopleWhoCanHelp";
import { clearPeopleCache } from "../lib/peopleClient";
import { derivePeopleView, PEOPLE_MESSAGES } from "../lib/peopleState";

/**
 * The production incident, reproduced against a mocked provider: three jobs, one
 * company failing for its own reasons, one empty, one successful. The successful
 * one must render, and no provider search may run until a user explicitly asks.
 */

const CISCO_JOB = 901;
const HII_JOB = 902;
const L3HARRIS_JOB = 903;

const scaffold = {
  beta: true,
  warnings: [],
  generated_at: "2026-07-26T12:00:00Z",
  search_scope: {
    company_scope: "Hiring company only",
    location_filter: "soft",
    parent_company_matches_included: false,
    refresh_eligible: false,
    exact_company_search_completed: true,
    related_company_search_attempted: false,
    broaden_eligible: false,
    broaden_attempted: false
  },
  controls: { email_discovery: false, outreach_drafting: false }
};

const emptyCategories = {
  likely_recruiters: [],
  potential_hiring_managers: [],
  potential_referrers: []
};

const l3harrisPerson = {
  recommendation_id: 55,
  full_name: "Alex Example",
  current_title: "Technical Recruiter",
  current_company: "L3Harris Technologies",
  category: "likely_recruiter",
  category_label: "Likely recruiter",
  relevance_score: 91,
  confidence: "high",
  current_employment_confidence: 0.94,
  employment_validation_status: "confirmed_exact_company_verified",
  employment_last_verified_at: "2026-07-25T12:00:00Z",
  employment_warning: null,
  email_lookup_allowed: true,
  reasons: ["Currently listed at the hiring company."],
  limitations: [],
  last_checked_at: "2026-07-25T12:00:00Z",
  professional_profile_url: "https://www.linkedin.com/in/alex-example",
  email_status: "not_requested",
  professional_email: null,
  email_verified_at: null,
  saved: false,
  contacted: false
};

/** Cisco fails request-scoped, HII is empty, L3Harris returns a recruiter. */
const PAYLOADS: Record<number, unknown> = {
  [CISCO_JOB]: {
    ...scaffold,
    status: "provider_unavailable",
    availability_reason: "provider_request_invalid",
    retry_eligible: false,
    provider_circuit: "closed",
    result_freshness: "none",
    categories: emptyCategories
  },
  [HII_JOB]: {
    ...scaffold,
    status: "no_reliable_matches",
    availability_reason: "available",
    provider_circuit: "closed",
    result_freshness: "none",
    categories: emptyCategories
  },
  [L3HARRIS_JOB]: {
    ...scaffold,
    status: "complete",
    availability_reason: "available",
    provider_circuit: "closed",
    result_freshness: "fresh",
    categories: { ...emptyCategories, likely_recruiters: [l3harrisPerson] }
  }
};

/** Reading a job's people state returns "nothing searched yet"; only an
 * explicit search returns that company's outcome. */
function installTransport() {
  const requested: string[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    requested.push(`${method} ${url}`);
    const jobId = Number(url.match(/\/jobs\/(\d+)\//)?.[1] ?? 0);
    const payload =
      method === "POST"
        ? PAYLOADS[jobId]
        : { ...scaffold, status: "not_started", result_freshness: "none", categories: emptyCategories };
    return Promise.resolve({
      ok: true,
      json: async () => payload,
      text: async () => JSON.stringify(payload)
    } as Response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    fetchMock,
    requested,
    posts: () => requested.filter((entry) => entry.startsWith("POST"))
  };
}

/** Three job workspaces open side by side — the isolation the incident broke. */
function renderThreeSections() {
  return render(
    <div>
      <div data-testid="cisco">
        <PeopleWhoCanHelp jobId={CISCO_JOB} />
      </div>
      <div data-testid="hii">
        <PeopleWhoCanHelp jobId={HII_JOB} />
      </div>
      <div data-testid="l3harris">
        <PeopleWhoCanHelp jobId={L3HARRIS_JOB} />
      </div>
    </div>
  );
}

async function findPeopleIn(testId: string) {
  const card = screen.getByTestId(testId);
  fireEvent.click(await within(card).findByRole("button", { name: "Find people" }));
  return card;
}

describe("People Who Can Help — three-company provider scenario", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("starts no provider search when three sections are opened", async () => {
    const transport = installTransport();
    renderThreeSections();

    // Each section reads its own stored state, which spends no quota, and none
    // of them starts a search on its own.
    expect(await screen.findAllByRole("button", { name: "Find people" })).toHaveLength(3);
    expect(transport.posts()).toEqual([]);
    expect(transport.requested).toEqual([
      `GET http://localhost:8000/jobs/${CISCO_JOB}/people`,
      `GET http://localhost:8000/jobs/${HII_JOB}/people`,
      `GET http://localhost:8000/jobs/${L3HARRIS_JOB}/people`
    ]);
  });

  it("renders L3Harris results even though Cisco failed and HII was empty", async () => {
    const transport = installTransport();
    renderThreeSections();

    const cisco = await findPeopleIn("cisco");
    const hii = await findPeopleIn("hii");
    const l3harris = await findPeopleIn("l3harris");

    // Cisco: a request-scoped provider rejection, with no misleading "paused"
    // language and no Retry offered.
    expect(
      await within(cisco).findByText("We could not complete this search because the provider request was invalid.")
    ).toBeInTheDocument();
    expect(within(cisco).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

    // Huntington Ingalls: a genuine empty result.
    expect(await within(hii).findByText(PEOPLE_MESSAGES.empty)).toBeInTheDocument();

    // L3Harris still returns its person.
    expect(await within(l3harris).findByText("Alex Example")).toBeInTheDocument();
    expect(within(l3harris).getByText(/1 recruiter/)).toBeInTheDocument();

    // Exactly one paid request per explicit click, each scoped to its own job.
    await waitFor(() => expect(transport.posts()).toHaveLength(3));
    expect(transport.posts()).toEqual([
      `POST http://localhost:8000/jobs/${CISCO_JOB}/people/discover`,
      `POST http://localhost:8000/jobs/${HII_JOB}/people/discover`,
      `POST http://localhost:8000/jobs/${L3HARRIS_JOB}/people/discover`
    ]);
  });

  it("never shows the old catch-all paused message for a non-outage failure", async () => {
    installTransport();
    renderThreeSections();
    await findPeopleIn("cisco");
    await findPeopleIn("hii");

    await screen.findByText(PEOPLE_MESSAGES.empty);
    expect(
      screen.queryByText(/temporarily paused after repeated provider failures/i)
    ).not.toBeInTheDocument();
  });

  it("keeps one card's failure out of another card's state", async () => {
    installTransport();
    renderThreeSections();

    const cisco = await findPeopleIn("cisco");
    await within(cisco).findByText("We could not complete this search because the provider request was invalid.");

    // The other two sections are still untouched and still unsearched.
    const hii = screen.getByTestId("hii");
    const l3harris = screen.getByTestId("l3harris");
    expect(within(hii).getByRole("button", { name: "Find people" })).toBeInTheDocument();
    expect(within(l3harris).getByRole("button", { name: "Find people" })).toBeInTheDocument();

    await findPeopleIn("l3harris");
    expect(await within(l3harris).findByText("Alex Example")).toBeInTheDocument();
  });

  it("does not create duplicate requests from a double-click", async () => {
    const transport = installTransport();
    renderThreeSections();

    const card = screen.getByTestId("l3harris");
    const button = await within(card).findByRole("button", { name: "Find people" });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    expect(await within(card).findByText("Alex Example")).toBeInTheDocument();
    expect(transport.posts()).toHaveLength(1);
  });
});

describe("derivePeopleView", () => {
  const base = {
    ...scaffold,
    status: "provider_unavailable" as const,
    categories: emptyCategories
  };

  it.each([
    ["company_domain_unresolved", "domain_unresolved", PEOPLE_MESSAGES.domain_unresolved],
    ["provider_rate_limited", "rate_limited", PEOPLE_MESSAGES.rate_limited],
    ["provider_unauthorized", "configuration_error", PEOPLE_MESSAGES.configuration_error],
    ["provider_circuit_open", "provider_unavailable", PEOPLE_MESSAGES.provider_unavailable]
  ])("maps %s onto %s", (reason, expectedState, expectedMessage) => {
    const view = derivePeopleView({
      data: { ...base, availability_reason: reason } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.state).toBe(expectedState);
    expect(view.message).toBe(expectedMessage);
  });

  it("offers Retry only for retryable states", () => {
    const retryable = derivePeopleView({
      data: { ...base, availability_reason: "provider_timeout", retry_eligible: true } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(retryable.canRetry).toBe(true);

    for (const reason of [
      "company_domain_unresolved",
      "provider_user_limit_exceeded",
      "provider_unauthorized"
    ]) {
      const view = derivePeopleView({
        data: { ...base, availability_reason: reason, retry_eligible: true } as any,
        error: null,
        loading: false,
        requested: true
      });
      expect(view.canRetry, reason).toBe(false);
    }
  });

  it("surfaces the retry time for a rate-limited provider", () => {
    const view = derivePeopleView({
      data: {
        ...base,
        availability_reason: "provider_rate_limited",
        retry_eligible: true,
        retry_after_seconds: 45
      } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.retryAfterSeconds).toBe(45);
  });

  it("labels stale results as cached", () => {
    const view = derivePeopleView({
      data: {
        ...base,
        status: "complete",
        result_freshness: "stale",
        categories: { ...emptyCategories, likely_recruiters: [l3harrisPerson] }
      } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.state).toBe("success");
    expect(view.cached).toBe(true);
  });
});

describe("PDL no-match versus a genuinely invalid request", () => {
  const base = {
    ...scaffold,
    categories: emptyCategories
  };

  it("renders a provider no-match as a neutral empty state", () => {
    const view = derivePeopleView({
      data: {
        ...base,
        status: "no_reliable_matches",
        availability_reason: "available"
      } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.state).toBe("empty");
    expect(view.message).toBe(
      "No strong recruiter, manager, or referral matches were found for this company yet."
    );
    // A truthful empty answer must not offer a retry that cannot change it.
    expect(view.canRetry).toBe(false);
  });

  it("never describes a no-match as a rejected request", () => {
    const view = derivePeopleView({
      data: {
        ...base,
        status: "no_reliable_matches",
        availability_reason: "available"
      } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.message).not.toMatch(/could not accept/i);
    expect(view.message).not.toMatch(/invalid/i);
  });

  it("keeps distinct copy for a genuinely invalid request", () => {
    const view = derivePeopleView({
      data: {
        ...base,
        status: "invalid_request",
        availability_reason: "provider_request_invalid"
      } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.state).toBe("invalid_request");
    expect(view.message).toBe(
      "We could not complete this search because the provider request was invalid."
    );
    expect(view.canRetry).toBe(false);
  });

  it("names an unresolved company in provider terms", () => {
    const view = derivePeopleView({
      data: {
        ...base,
        status: "domain_unresolved",
        availability_reason: "company_domain_unresolved"
      } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.message).toBe(
      "We could not confidently identify this company in the people provider."
    );
  });

  it("keeps partial results visible with a quiet coverage note", () => {
    const view = derivePeopleView({
      data: {
        ...base,
        status: "partial",
        availability_reason: "available",
        categories: { ...emptyCategories, likely_recruiters: [l3harrisPerson] }
      } as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(view.state).toBe("partial");
    expect(view.message).toBe("Some categories did not have strong matches.");
    expect(view.canRetry).toBe(false);
  });

  it("renders partial results and their note in the section", async () => {
    const payload = {
      ...base,
      status: "partial",
      availability_reason: "available",
      result_freshness: "fresh",
      categories: { ...emptyCategories, likely_recruiters: [l3harrisPerson] }
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => payload,
          text: async () => JSON.stringify(payload)
        } as Response)
      )
    );
    render(<PeopleWhoCanHelp jobId={950} />);

    expect(await screen.findByText("Alex Example")).toBeInTheDocument();
    expect(
      screen.getByText("Some categories did not have strong matches.")
    ).toBeInTheDocument();
  });

  it("shows a no-match state without any failure styling or retry", async () => {
    const payload = {
      ...base,
      status: "no_reliable_matches",
      availability_reason: "available",
      result_freshness: "none"
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => payload,
          text: async () => JSON.stringify(payload)
        } as Response)
      )
    );
    render(<PeopleWhoCanHelp jobId={951} />);

    const message = await screen.findByText(
      "No strong recruiter, manager, or referral matches were found for this company yet."
    );
    expect(message).toBeInTheDocument();
    // Neutral muted copy, not an alert.
    expect(message.getAttribute("role")).toBeNull();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });
});
