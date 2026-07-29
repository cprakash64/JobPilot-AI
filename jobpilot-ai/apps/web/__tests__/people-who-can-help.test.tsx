import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PeopleWhoCanHelp } from "../components/PeopleWhoCanHelp";
import { clearPeopleCache } from "../lib/peopleClient";

const recommendation = {
  recommendation_id: 11,
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
  reasons: ["Currently listed at the hiring company.", "Has a relevant recruiting title."],
  limitations: ["Recruiting responsibility for this opening has not been confirmed."],
  last_checked_at: "2026-07-25T12:00:00Z",
  professional_profile_url: "https://www.linkedin.com/in/rita-recruiter",
  email_status: "not_requested",
  professional_email: null,
  email_verified_at: null,
  saved: false,
  contacted: false
};

function response(overrides = {}) {
  return {
    status: "complete",
    beta: true,
    categories: {
      likely_recruiters: [recommendation],
      potential_hiring_managers: [],
      potential_referrers: []
    },
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
    controls: { email_discovery: true, outreach_drafting: true },
    ...overrides
  };
}

describe("PeopleWhoCanHelp", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps the section visible with an understandable globally-disabled state", async () => {
    const disabled = response({
      status: "disabled",
      availability_reason: "globally_disabled",
      beta: false,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => disabled,
      text: async () => JSON.stringify(disabled)
    }));
    render(<PeopleWhoCanHelp jobId="7" />);
    expect(await screen.findByRole("heading", { name: "People Who Can Help" })).toBeInTheDocument();
    expect(
      await screen.findByText("People recommendations are not enabled for this account.")
    ).toBeInTheDocument();
  });

  it("shows a stable loading state while checking for prior discovery", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
    render(<PeopleWhoCanHelp jobId={7} />);
    expect(screen.getByRole("heading", { name: "People Who Can Help" })).toBeInTheDocument();
    expect(screen.getByText("Checking for saved results…")).toBeInTheDocument();
  });

  it.each([
    [
      "provider_unavailable",
      "provider_unauthorized",
      "People search is temporarily unavailable because the provider connection needs attention."
    ],
    [
      "provider_configuration_error",
      "provider_forbidden",
      "People search is temporarily unavailable because the provider connection needs attention."
    ],
    [
      "provider_unavailable",
      "provider_rate_limited",
      "The people provider is temporarily rate-limited. Try again after the displayed time."
    ],
    [
      "provider_unavailable",
      "provider_timeout",
      "The people provider is temporarily unavailable."
    ],
    [
      "provider_unavailable",
      "provider_circuit_open",
      "The people provider is temporarily unavailable."
    ],
    [
      "domain_unresolved",
      "company_domain_unresolved",
      "We could not confidently identify this company in the people provider."
    ],
    [
      "user_budget_exhausted",
      "user_daily_limit_reached",
      "You have used all of today's people searches."
    ],
    [
      // A provider cost stop is never phrased as the user's own limit.
      "provider_budget_exhausted",
      "provider_budget_exceeded",
      "People search is temporarily unavailable because the provider budget has been reached."
    ]
  ])("names the real cause for %s/%s", async (status, availabilityReason, expected) => {
    const unavailable = response({
      status,
      availability_reason: availabilityReason,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => unavailable,
      text: async () => JSON.stringify(unavailable)
    }));
    render(<PeopleWhoCanHelp jobId={7} />);
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("keeps browser refresh after provider_schema_error read-only", async () => {
    const unavailable = response({
      status: "provider_unavailable",
      availability_reason: "provider_schema_error",
      retry_eligible: false,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => unavailable,
      text: async () => JSON.stringify(unavailable)
    });
    vi.stubGlobal("fetch", fetchMock);
    const first = render(<PeopleWhoCanHelp jobId={7596} />);
    expect(
      await screen.findByText("The people provider returned an unsupported response.")
    ).toBeInTheDocument();
    first.unmount();
    clearPeopleCache();
    render(<PeopleWhoCanHelp jobId={7596} />);
    expect(
      await screen.findByText("The people provider returned an unsupported response.")
    ).toBeInTheDocument();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")
    ).toHaveLength(0);
  });

  it("shows the discovery initial state, starts discovery, and renders all result categories", async () => {
    const manager = {
      ...recommendation,
      recommendation_id: 12,
      full_name: "Morgan Manager",
      current_title: "Director of Machine Learning",
      category: "potential_hiring_manager",
      category_label: "Potential hiring manager"
    };
    const referrer = {
      ...recommendation,
      recommendation_id: 13,
      full_name: "Pat Referrer",
      current_title: "Machine Learning Engineer",
      category: "potential_referrer",
      category_label: "Potential referral candidate",
      professional_profile_url: null
    };
    const initial = response({
      status: "not_started",
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const discovered = response({
      categories: {
        likely_recruiters: [recommendation],
        potential_hiring_managers: [manager],
        potential_referrers: [referrer]
      }
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => initial,
        text: async () => JSON.stringify(initial)
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => discovered,
        text: async () => JSON.stringify(discovered)
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={731} />);

    expect(await screen.findByText(/Find recruiters and referral candidates/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Find people" }));

    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(screen.getByText("Morgan Manager")).toBeInTheDocument();
    expect(screen.getByText("Pat Referrer")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Likely Recruiters" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Potential Hiring Managers" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Potential Referral Candidates" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8000/jobs/731/people/discover",
      expect.objectContaining({ method: "POST" })
    );
  });

  it.each([
    [
      "no reliable matches",
      response({
        status: "no_reliable_matches",
        warnings: ["No sufficiently reliable people were found."],
        categories: {
          likely_recruiters: [],
          potential_hiring_managers: [],
          potential_referrers: []
        }
      }),
      /No reliable recruiting contact met JobPilot’s threshold/
    ],
    [
      "provider unavailable",
      response({
        status: "provider_unavailable",
        warnings: [],
        categories: {
          likely_recruiters: [],
          potential_hiring_managers: [],
          potential_referrers: []
        }
      }),
      /people provider is temporarily unavailable/
    ]
  ])("renders the %s state without inventing recommendations", async (_label, payload, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
      text: async () => JSON.stringify(payload)
    }));
    render(<PeopleWhoCanHelp jobId={7} />);
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText("Rita Recruiter")).not.toBeInTheDocument();
    if (payload.status === "no_reliable_matches") {
      expect(screen.queryByRole("button", { name: "Find people" })).not.toBeInTheDocument();
    } else {
      expect(screen.getByRole("button", { name: "Retry discovery" })).toBeInTheDocument();
    }
  });

  it("runs one controlled broaden request only after an eligible user action", async () => {
    const noMatch = response({
      status: "no_reliable_matches",
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      },
      search_scope: {
        company_scope: "Hiring company only",
        location_filter: "soft",
        parent_company_matches_included: false,
        refresh_eligible: false,
        exact_company_search_completed: true,
        related_company_search_attempted: false,
        broaden_eligible: true,
        broaden_attempted: false
      }
    });
    const broadened = response({
      search_scope: {
        company_scope: "Hiring company and evidence-backed related domain",
        location_filter: "soft",
        parent_company_matches_included: true,
        refresh_eligible: false,
        exact_company_search_completed: true,
        related_company_search_attempted: true,
        broaden_eligible: false,
        broaden_attempted: true
      }
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => noMatch,
        text: async () => JSON.stringify(noMatch)
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => broadened,
        text: async () => JSON.stringify(broadened)
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={7606} />);

    expect(
      await screen.findByText("No reliable recruiting contact met JobPilot’s threshold.")
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const button = screen.getByRole("button", { name: "Broaden search" });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/jobs/7606/people/broaden",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("keeps the section visible and reports an API failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network unavailable")));
    render(<PeopleWhoCanHelp jobId={7} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "People Who Can Help" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows a distinct selected-beta message for a cohort-excluded account", async () => {
    const excluded = response({
      status: "disabled",
      availability_reason: "not_in_rollout",
      beta: false,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => excluded,
      text: async () => JSON.stringify(excluded)
    }));
    render(<PeopleWhoCanHelp jobId={7} />);
    expect(
      await screen.findByText("People recommendations are currently available to selected beta users.")
    ).toBeInTheDocument();
  });

  it("shows a safe configuration-unavailable state", async () => {
    const unavailable = response({
      status: "disabled",
      availability_reason: "configuration_unavailable",
      beta: false,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => unavailable,
      text: async () => JSON.stringify(unavailable)
    }));
    render(<PeopleWhoCanHelp jobId={7} />);
    expect(
      await screen.findByText("People recommendations are temporarily unavailable.")
    ).toBeInTheDocument();
  });

  it("renders grounded evidence, limitations, empty categories, safe actions, and beta label", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => response(),
      text: async () => JSON.stringify(response())
    }));
    render(<PeopleWhoCanHelp jobId="7" />);
    expect(await screen.findByRole("heading", { name: "People Who Can Help" })).toBeInTheDocument();
    expect(await screen.findByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Rita Recruiter")).toBeInTheDocument();
    expect(screen.getByText(/responsibility for this opening has not been confirmed/i)).toBeInTheDocument();
    expect(screen.getByText("No potential manager met JobPilot’s confidence threshold.")).toBeInTheDocument();
    expect(screen.getByText("No relevant employee met JobPilot’s referral threshold.")).toBeInTheDocument();
    expect(screen.getByText("Scope: Hiring company only")).toBeInTheDocument();
    expect(screen.getByText("Location used as a soft signal")).toBeInTheDocument();
    expect(screen.getByText("Related-company matches were not included")).toBeInTheDocument();
    expect(screen.getByText("Using current cached search")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /LinkedIn/ })).toHaveAttribute(
      "rel", "noopener noreferrer"
    );
    expect(screen.getByRole("button", { name: /Find work email/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Draft message" })).toBeInTheDocument();
  });

  it("does not render a profile action for a non-allowlisted URL", async () => {
    const unsafe = response({
      categories: {
        likely_recruiters: [{
          ...recommendation,
          professional_profile_url: "https://profiles.example.com/in/rita-recruiter"
        }],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => unsafe,
      text: async () => JSON.stringify(unsafe)
    }));
    render(<PeopleWhoCanHelp jobId={7} />);
    await screen.findByText("Rita Recruiter");
    expect(screen.queryByRole("link", { name: /LinkedIn/ })).not.toBeInTheDocument();
    // An honest disabled affordance replaces the link — never a guessed URL.
    expect(screen.getByTitle(/never guesses a profile URL/)).toBeInTheDocument();
  });

  it("shows employment verification and blocks email lookup when employment conflicts", async () => {
    const conflicted = response({
      categories: {
        likely_recruiters: [{
          ...recommendation,
          current_employment_confidence: 0.1,
          employment_validation_status: "conflicting_current_employment",
          employment_warning: "Current employment needs revalidation.",
          email_lookup_allowed: false,
          email_status: "employment_conflict"
        }],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => conflicted,
      text: async () => JSON.stringify(conflicted)
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={7} />);

    expect(await screen.findByText("Current employment needs revalidation.")).toBeInTheDocument();
    expect(screen.getByText(/High confidence · employment verified/)).toBeInTheDocument();
    expect(screen.getByText(/Work email is unavailable until current employment is revalidated/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Find work email/ })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows the honest unverified-employment warning while allowing explicit email lookup", async () => {
    const unverified = response({
      categories: {
        likely_recruiters: [{
          ...recommendation,
          employment_validation_status: "exact_company_current_but_unverified_freshness",
          employment_warning:
            "Currently listed at the hiring company. Current employment has not been independently verified.",
          email_lookup_allowed: true
        }],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => unverified,
      text: async () => JSON.stringify(unverified)
    }));

    render(<PeopleWhoCanHelp jobId={7} />);

    expect(await screen.findByText(
      "Currently listed at the hiring company. Current employment has not been independently verified."
    )).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Find work email/ })).toBeInTheDocument();
  });

  it("opens an editable manual-review dialog for a grounded draft", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => response(),
        text: async () => JSON.stringify(response())
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          message_type: "linkedin_message",
          subject: null,
          body: "Hi Rita,\\n\\nI’m applying for the role.",
          facts_used: ["job:Machine Learning Engineer"],
          assumptions: [],
          omitted_uncertain_facts: ["recruiter_assignment_unconfirmed"],
          character_count: 43,
          requires_manual_review: true,
          sent: false
        }),
        text: async () => JSON.stringify({
          message_type: "linkedin_message",
          subject: null,
          body: "Hi Rita,\\n\\nI’m applying for the role.",
          facts_used: ["job:Machine Learning Engineer"],
          assumptions: [],
          omitted_uncertain_facts: ["recruiter_assignment_unconfirmed"],
          character_count: 43,
          requires_manual_review: true,
          sent: false
        })
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId="7" />);
    fireEvent.click(await screen.findByRole("button", { name: "Draft message" }));
    expect(await screen.findByRole("dialog", { name: "Review outreach draft" })).toBeInTheDocument();
    expect(screen.getByText(/never sends this message automatically/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Outreach draft")).toHaveValue(
      "Hi Rita,\\n\\nI’m applying for the role."
    );
    expect(screen.getByLabelText("Draft tone")).toHaveValue("concise");
    expect(screen.getByRole("button", { name: "Regenerate draft" })).toBeInTheDocument();
  });
});

/**
 * The People panel now lives in the Jobs detail workspace (Networking section)
 * instead of inside every job card. It reads stored results when it is opened —
 * which costs no quota — and every provider-backed search stays an explicit,
 * de-duplicated click.
 */
describe("PeopleWhoCanHelp in the job workspace", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reads stored results on open, discovers once on click, and looks up email only on click", async () => {
    const referrer = {
      ...recommendation,
      recommendation_id: 13,
      full_name: "Pat Referrer",
      current_title: "Machine Learning Engineer",
      category: "potential_referrer",
      category_label: "Potential referral candidate",
      professional_profile_url: null
    };
    const initial = response({
      status: "not_started",
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const discovered = response({
      categories: {
        likely_recruiters: [recommendation],
        potential_hiring_managers: [],
        potential_referrers: [referrer]
      }
    });
    const verified = response({
      categories: {
        likely_recruiters: [{
          ...recommendation,
          email_status: "verified",
          professional_email: "rita@acme.example",
          email_verified_at: "2026-07-25T12:05:00Z"
        }],
        potential_hiring_managers: [],
        potential_referrers: [referrer]
      }
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/email")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: "verified",
            professional_email: "rita@acme.example",
            verified_at: "2026-07-25T12:05:00Z"
          }),
          text: async () => "{}"
        } as Response);
      }
      if (init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () => discovered,
          text: async () => JSON.stringify(discovered)
        } as Response);
      }
      const body = fetchMock.mock.calls.some(([, options]) => options?.method === "POST")
        ? verified
        : initial;
      return Promise.resolve({
        ok: true,
        json: async () => body,
        text: async () => JSON.stringify(body)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={731} />);

    // Opening the section reads what is already stored. No search is started.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/jobs/731/people",
      expect.any(Object)
    );
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Find people" }));

    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(screen.getByText("Pat Referrer")).toBeInTheDocument();
    expect(screen.getByText("1 recruiter · 0 potential managers · 1 referral candidate")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /LinkedIn/ })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/rita-recruiter"
    );
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/people/discover"))
    ).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/email"))).toBe(false);

    fireEvent.click(screen.getAllByRole("button", { name: "Find work email" })[0]);
    expect(await screen.findByText(/Verified work email: rita@acme.example/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/jobs/731/people/11/email",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("coalesces repeated activation into one paid discovery mutation", async () => {
    const initial = response({
      status: "not_started",
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    let resolveDiscovery!: (value: Response) => void;
    const discoveryResponse = new Promise<Response>((resolve) => {
      resolveDiscovery = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return discoveryResponse;
      return Promise.resolve({
        ok: true,
        json: async () => initial,
        text: async () => JSON.stringify(initial)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={812} />);

    const button = await screen.findByRole("button", { name: "Find people" });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => {
      const discoveryCalls = fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith("/jobs/812/people/discover") && init?.method === "POST"
      );
      expect(discoveryCalls).toHaveLength(1);
    });
    resolveDiscovery({
      ok: true,
      json: async () => response(),
      text: async () => JSON.stringify(response())
    } as Response);
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
  });

  it("coalesces repeated explicit provider retries into one mutation", async () => {
    const unavailable = response({
      status: "provider_unavailable",
      availability_reason: "provider_timeout",
      retry_eligible: true,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    let resolveDiscovery!: (value: Response) => void;
    const discoveryResponse = new Promise<Response>((resolve) => {
      resolveDiscovery = resolve;
    });
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return discoveryResponse;
      return Promise.resolve({
        ok: true,
        json: async () => unavailable,
        text: async () => JSON.stringify(unavailable)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={7596} />);

    const retry = await screen.findByRole("button", { name: "Retry discovery" });
    fireEvent.click(retry);
    fireEvent.click(retry);

    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    });
    resolveDiscovery({
      ok: true,
      json: async () => response(),
      text: async () => JSON.stringify(response())
    } as Response);
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
  });

  it("coalesces repeated explicit persistence retries into one mutation", async () => {
    const persistenceFailure = response({
      status: "persistence_error",
      availability_reason: "recommendation_commit_failed",
      retry_eligible: true,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    let resolveDiscovery!: (value: Response) => void;
    const discoveryResponse = new Promise<Response>((resolve) => {
      resolveDiscovery = resolve;
    });
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return discoveryResponse;
      return Promise.resolve({
        ok: true,
        json: async () => persistenceFailure,
        text: async () => JSON.stringify(persistenceFailure)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={7601} />);

    const retry = await screen.findByRole("button", { name: "Retry discovery" });
    fireEvent.click(retry);
    fireEvent.click(retry);

    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    });
    resolveDiscovery({
      ok: true,
      json: async () => response(),
      text: async () => JSON.stringify(response())
    } as Response);
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
  });

  it("renders a stale persisted state and refreshes only after explicit action", async () => {
    const stale = response({
      status: "stale",
      warnings: ["Contact discovery has been upgraded. Refresh to check again."],
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        json: async () => (init?.method === "POST" ? response() : stale),
        text: async () => JSON.stringify(init?.method === "POST" ? response() : stale)
      } as Response)
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={7506} />);

    expect(
      await screen.findByText("Contact discovery has been upgraded. Refresh to check again.")
    ).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Broaden search" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh people" }));
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("does not offer a retry for a non-retryable provider account limitation", async () => {
    const forbidden = response({
      status: "provider_unavailable",
      availability_reason: "provider_forbidden",
      retry_eligible: false,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => forbidden,
      text: async () => JSON.stringify(forbidden)
    }));
    render(<PeopleWhoCanHelp jobId={7508} />);

    expect(
      await screen.findByText(
        "People search is temporarily unavailable because the provider connection needs attention."
      )
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find people" })).not.toBeInTheDocument();
  });

  it("does not offer a retry when complete-profile access requires a master key", async () => {
    const data = response({
      status: "provider_unavailable",
      availability_reason: "provider_master_key_required_or_forbidden",
      retry_eligible: false,
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      },
      warnings: ["Apollo complete-profile access is unavailable for the configured account."]
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => data,
      text: async () => JSON.stringify(data)
    }));
    render(<PeopleWhoCanHelp jobId={7602} />);

    expect(
      await screen.findByText(
        "Apollo complete-profile access is unavailable for the configured account."
      )
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry discovery" })).not.toBeInTheDocument();
  });

  it.each([
    [
      "disabled",
      response({
        status: "disabled",
        availability_reason: "globally_disabled",
        beta: false,
        categories: {
          likely_recruiters: [],
          potential_hiring_managers: [],
          potential_referrers: []
        }
      }),
      "People recommendations are not enabled for this account."
    ],
    [
      "cohort excluded",
      response({
        status: "disabled",
        availability_reason: "not_in_rollout",
        beta: false,
        categories: {
          likely_recruiters: [],
          potential_hiring_managers: [],
          potential_referrers: []
        }
      }),
      "People recommendations are currently available to selected beta users."
    ],
    [
      "provider unavailable",
      response({
        status: "provider_unavailable",
        categories: {
          likely_recruiters: [],
          potential_hiring_managers: [],
          potential_referrers: []
        }
      }),
      "The people provider is temporarily unavailable."
    ],
    [
      "no results",
      response({
        status: "no_reliable_matches",
        categories: {
          likely_recruiters: [],
          potential_hiring_managers: [],
          potential_referrers: []
        }
      }),
      "No reliable recruiting contact met JobPilot’s threshold."
    ]
  ])("shows the %s state when the section is opened", async (_label, payload, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
      text: async () => JSON.stringify(payload)
    }));
    render(<PeopleWhoCanHelp jobId={7} />);
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("shows a retry after an API failure and recovers without extra requests", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => response(),
        text: async () => JSON.stringify(response())
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelp jobId={7300} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "People recommendations could not be loaded"
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("renders every person in each category, not a two-per-category preview", async () => {
    const person = (recommendationId: number, fullName: string, category: string) => ({
      ...recommendation,
      recommendation_id: recommendationId,
      full_name: fullName,
      category
    });
    const current = response({
      categories: {
        likely_recruiters: [
          person(101, "Recruiter One", "likely_recruiter"),
          person(102, "Recruiter Two", "likely_recruiter"),
          person(103, "Recruiter Three", "likely_recruiter")
        ],
        potential_hiring_managers: [
          person(201, "Manager One", "potential_hiring_manager"),
          person(202, "Manager Two", "potential_hiring_manager")
        ],
        potential_referrers: [person(301, "Referrer One", "potential_referrer")]
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => current,
      text: async () => JSON.stringify(current)
    }));
    render(<PeopleWhoCanHelp jobId={7800} />);

    expect(await screen.findByText("Recruiter One")).toBeInTheDocument();
    expect(screen.getByText("Recruiter Two")).toBeInTheDocument();
    expect(screen.getByText("Recruiter Three")).toBeInTheDocument();
    expect(screen.getByText("Manager One")).toBeInTheDocument();
    expect(screen.getByText("Manager Two")).toBeInTheDocument();
    expect(screen.getByText("Referrer One")).toBeInTheDocument();
    expect(screen.getByText("3 recruiters · 2 potential managers · 1 referral candidate")).toBeInTheDocument();
  });
});
