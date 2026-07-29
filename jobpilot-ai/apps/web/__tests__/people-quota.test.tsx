import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PeopleWhoCanHelp } from "../components/PeopleWhoCanHelp";
import { clearPeopleCache } from "../lib/peopleClient";
import {
  derivePeopleView,
  formatResetTime,
  PEOPLE_MESSAGES,
  quotaExhaustedMessage,
  quotaSummary
} from "../lib/peopleState";

/**
 * A user's people-search allowance is counted in deliberate actions. One
 * "Find people" is one search no matter how many provider calls it makes
 * internally, and reading results never spends anything.
 */

const RESETS_AT = "2026-07-30T00:00:00.000Z";

function quota(overrides: Record<string, unknown> = {}) {
  return {
    daily_limit: 20,
    daily_used: 8,
    daily_remaining: 12,
    resets_at: RESETS_AT,
    hourly_limit: 10,
    broadened_search_cost: 1,
    ...overrides
  };
}

const emptyCategories = {
  likely_recruiters: [],
  potential_hiring_managers: [],
  potential_referrers: []
};

const person = {
  recommendation_id: 501,
  full_name: "Robin Recruiter",
  current_title: "Technical Recruiter",
  current_company: "AECOM",
  category: "likely_recruiter",
  category_label: "Likely recruiter",
  relevance_score: 88,
  confidence: "high",
  current_employment_confidence: 0.8,
  employment_validation_status: "exact_company_current_but_unverified_freshness",
  employment_last_verified_at: "2026-07-28T12:00:00Z",
  employment_warning: null,
  email_lookup_allowed: true,
  reasons: ["Currently listed at the hiring company."],
  limitations: [],
  last_checked_at: "2026-07-28T12:00:00Z",
  professional_profile_url: null,
  email_status: "not_requested",
  professional_email: null,
  email_verified_at: null,
  saved: false,
  contacted: false
};

function peopleResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "not_started",
    availability_reason: "available",
    result_freshness: "none",
    beta: true,
    warnings: [],
    generated_at: null,
    quota: quota(),
    search_scope: {
      company_scope: "Hiring company only",
      location_filter: "soft",
      parent_company_matches_included: false,
      refresh_eligible: false,
      exact_company_search_completed: false,
      related_company_search_attempted: false,
      broaden_eligible: false,
      broaden_attempted: false
    },
    categories: emptyCategories,
    controls: { email_discovery: false, outreach_drafting: false },
    ...overrides
  };
}

function ok(payload: unknown) {
  return Promise.resolve({
    ok: true,
    headers: new Headers(),
    json: async () => payload,
    text: async () => JSON.stringify(payload)
  } as Response);
}

afterEach(() => {
  cleanup();
  clearPeopleCache();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("quota copy", () => {
  it("summarises the remaining allowance", () => {
    expect(quotaSummary(quota())).toBe("12 of 20 people searches remaining today.");
  });

  it("omits the summary when no limit is configured", () => {
    expect(quotaSummary(quota({ daily_limit: 0 }))).toBeNull();
    expect(quotaSummary(undefined)).toBeNull();
  });

  it("names the limit and the reset when exhausted", () => {
    const message = quotaExhaustedMessage(quota({ daily_remaining: 0, daily_used: 20 }));
    expect(message).toContain("You have used all 20 people searches for today.");
    expect(message).toContain("Your limit resets");
  });

  it("formats the reset timestamp", () => {
    expect(formatResetTime(RESETS_AT)).toMatch(/\w+ \d+ at /);
    expect(formatResetTime("not-a-date")).toBeNull();
    expect(formatResetTime(undefined)).toBeNull();
  });

  it("keeps the provider-budget message distinct from the user's limit", () => {
    const providerBudget = derivePeopleView({
      data: peopleResponse({
        status: "provider_budget_exhausted",
        availability_reason: "provider_budget_exceeded"
      }) as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(providerBudget.state).toBe("provider_budget_exhausted");
    expect(providerBudget.message).toBe(PEOPLE_MESSAGES.provider_budget_exhausted);
    expect(providerBudget.message).not.toMatch(/your|you have used/i);

    const userLimit = derivePeopleView({
      data: peopleResponse({
        status: "user_budget_exhausted",
        availability_reason: "user_daily_limit_reached",
        quota: quota({ daily_remaining: 0, daily_used: 20 })
      }) as any,
      error: null,
      loading: false,
      requested: true
    });
    expect(userLimit.state).toBe("budget_exhausted");
    expect(userLimit.message).toContain("all 20 people searches");
  });
});

describe("quota in the networking section", () => {
  it("shows the remaining count before any search is run", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => ok(peopleResponse()));
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={771} />);

    // Reading the allowance costs nothing, so it is visible before the user
    // spends a search.
    expect(
      await screen.findByText("12 of 20 people searches remaining today.")
    ).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("decrements by exactly one for a search with many provider calls", async () => {
    const searched = peopleResponse({
      status: "complete",
      result_freshness: "fresh",
      categories: { ...emptyCategories, likely_recruiters: [person] },
      // The backend performed 7 provider calls for this one action.
      quota: quota({ daily_used: 9, daily_remaining: 11 })
    });
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      ok(init?.method === "POST" ? searched : peopleResponse())
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={772} />);

    fireEvent.click(await screen.findByRole("button", { name: "Find people" }));

    expect(await screen.findByText("Robin Recruiter")).toBeInTheDocument();
    expect(await screen.findByText(/11 searches remaining · Resets/)).toBeInTheDocument();
    // One explicit click, one paid request.
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1)
    );
  });

  it("keeps results reachable at zero remaining", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      ok(
        peopleResponse({
          status: "complete",
          result_freshness: "fresh",
          categories: { ...emptyCategories, likely_recruiters: [person] },
          quota: quota({ daily_used: 20, daily_remaining: 0 })
        })
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={773} />);

    expect(await screen.findByText("Robin Recruiter")).toBeInTheDocument();
    expect(screen.getByText("0 of 20 people searches remaining today.")).toBeInTheDocument();
    // Existing results are served from storage without a new search.
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("shows the exhausted message with its reset date", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        ok(
          peopleResponse({
            status: "user_budget_exhausted",
            availability_reason: "user_daily_limit_reached",
            quota: quota({ daily_used: 20, daily_remaining: 0 })
          })
        )
      )
    );
    render(<PeopleWhoCanHelp jobId={774} />);

    const message = await screen.findByText(/You have used all 20 people searches/);
    expect(message).toBeInTheDocument();
    expect(message.textContent).toContain("Your limit resets");
  });

  it("uses provider-budget copy when the provider budget is the blocker", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        ok(
          peopleResponse({
            status: "provider_budget_exhausted",
            availability_reason: "provider_budget_exceeded"
          })
        )
      )
    );
    render(<PeopleWhoCanHelp jobId={775} />);

    expect(
      await screen.findByText(PEOPLE_MESSAGES.provider_budget_exhausted)
    ).toBeInTheDocument();
    expect(screen.queryByText(/You have used all/)).not.toBeInTheDocument();
  });

  it("spends nothing when the section is closed and opened again", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      ok(
        peopleResponse({
          status: "complete",
          result_freshness: "fresh",
          categories: { ...emptyCategories, likely_recruiters: [person] },
          quota: quota({ daily_used: 9, daily_remaining: 11 })
        })
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<PeopleWhoCanHelp jobId={776} />);
    expect(await screen.findByText("Robin Recruiter")).toBeInTheDocument();

    view.unmount();
    render(<PeopleWhoCanHelp jobId={776} />);
    expect(await screen.findByText("Robin Recruiter")).toBeInTheDocument();

    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(screen.getByText(/11 searches remaining/)).toBeInTheDocument();
  });
});

describe("broaden search cost", () => {
  it("states the cost before the user commits", async () => {
    const noMatch = peopleResponse({
      status: "no_reliable_matches",
      availability_reason: "available",
      quota: quota({ daily_used: 9, daily_remaining: 11 }),
      search_scope: {
        ...peopleResponse().search_scope,
        exact_company_search_completed: true,
        broaden_eligible: true
      }
    });
    vi.stubGlobal("fetch", vi.fn(() => ok(noMatch)));
    render(<PeopleWhoCanHelp jobId={777} />);

    expect(
      await screen.findByText(/Broaden search uses 1 additional people search/)
    ).toBeInTheDocument();
    const section = screen.getByRole("region", { name: /People Who Can Help/i });
    expect(within(section).getByRole("button", { name: "Broaden search" })).toBeInTheDocument();
  });
});
