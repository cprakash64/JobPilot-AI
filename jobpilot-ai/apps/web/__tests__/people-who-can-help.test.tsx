import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PeopleWhoCanHelp, PeopleWhoCanHelpSummary } from "../components/PeopleWhoCanHelp";
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
    ["provider_unauthorized", "The people data provider credentials could not be verified."],
    ["provider_forbidden", "The configured provider account does not have access to people search."],
    [
      "provider_master_key_required_or_forbidden",
      "Apollo complete-profile access is unavailable for the configured account."
    ],
    ["provider_rate_limited", "The people data provider rate limit has been reached."],
    ["provider_timeout", "The people search provider took too long to respond."],
    ["provider_circuit_open", "People search is temporarily paused after repeated provider failures."],
    ["provider_schema_error", "The people provider returned an unsupported response."],
    ["provider_request_invalid", "The people provider could not accept the profile request."],
    ["provider_response_invalid", "The people provider returned an unsupported response."]
  ])("shows the safe %s message", async (availabilityReason, expected) => {
    const unavailable = response({
      status: "provider_unavailable",
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
      /professional data provider is temporarily unavailable/
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
    expect(screen.getByRole("link", { name: /LinkedIn profile/ })).toHaveAttribute(
      "rel", "noopener noreferrer"
    );
    expect(screen.getByRole("button", { name: /Find work email/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Draft LinkedIn message/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Draft email/ })).toBeInTheDocument();
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
    expect(screen.queryByRole("link", { name: "LinkedIn profile" })).not.toBeInTheDocument();
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
    expect(screen.getByText("Current employment confidence: 10%")).toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("button", { name: /Draft LinkedIn message/ }));
    expect(await screen.findByRole("dialog", { name: "Review outreach draft" })).toBeInTheDocument();
    expect(screen.getByText(/never sends this message automatically/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Outreach draft")).toHaveValue(
      "Hi Rita,\\n\\nI’m applying for the role."
    );
    expect(screen.getByLabelText("Draft tone")).toHaveValue("concise");
    expect(screen.getByRole("button", { name: "Regenerate draft" })).toBeInTheDocument();
  });
});

describe("PeopleWhoCanHelpSummary", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders 100 collapsed card affordances without any people or discovery request", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <>
        {Array.from({ length: 100 }, (_, index) => (
          <PeopleWhoCanHelpSummary key={index} jobId={index + 1} onViewAll={() => undefined} />
        ))}
      </>
    );
    expect(screen.getAllByRole("heading", { name: "People Who Can Help" })).toHaveLength(100);
    expect(screen.getAllByRole("button", { name: "View people" })).toHaveLength(100);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads one persisted job on click, discovers once, renders counts/top people, and looks up email only on click", async () => {
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
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          status: "verified",
          professional_email: "rita@acme.example",
          verified_at: "2026-07-25T12:05:00Z"
        }),
        text: async () => "{}"
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => verified,
        text: async () => JSON.stringify(verified)
      });
    vi.stubGlobal("fetch", fetchMock);
    const onViewAll = vi.fn();
    render(<PeopleWhoCanHelpSummary jobId={731} onViewAll={onViewAll} />);

    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    expect(await screen.findByText("Find recruiters and referral candidates for this job.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Find people" }));

    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(screen.getByText("Pat Referrer")).toBeInTheDocument();
    expect(screen.getByText("1 recruiter · 0 potential managers · 1 referral candidate")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "LinkedIn profile" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/rita-recruiter"
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/jobs/731/people",
      expect.any(Object)
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8000/jobs/731/people/discover",
      expect.objectContaining({ method: "POST" })
    );
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/email"))).toBe(false);

    fireEvent.click(screen.getAllByRole("button", { name: "Find work email" })[0]);
    expect(await screen.findByText(/Verified work email: rita@acme.example/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://localhost:8000/jobs/731/people/11/email",
      expect.objectContaining({ method: "POST" })
    );

    fireEvent.click(screen.getByRole("button", { name: "View all people" }));
    expect(onViewAll).toHaveBeenCalledOnce();
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
    render(<PeopleWhoCanHelpSummary jobId={812} onViewAll={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    const button = await screen.findByRole("button", { name: "Find people" });
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

  it("coalesces repeated explicit provider Retry clicks into one mutation", async () => {
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
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return discoveryResponse;
      return Promise.resolve({
        ok: true,
        json: async () => unavailable,
        text: async () => JSON.stringify(unavailable)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelpSummary jobId={7596} onViewAll={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    const retry = await screen.findByRole("button", { name: "Retry discovery" });
    fireEvent.click(retry);
    fireEvent.click(retry);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")
      ).toHaveLength(1);
    });
    resolveDiscovery({
      ok: true,
      json: async () => response(),
      text: async () => JSON.stringify(response())
    } as Response);
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
  });

  it("keeps a cached persistence failure read-only until retry is eligible", async () => {
    const persistenceFailure = response({
      status: "persistence_error",
      availability_reason: "recommendation_commit_failed",
      retry_eligible: false,
      retry_eligible_at: "2026-07-26T12:05:00Z",
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => persistenceFailure,
      text: async () => JSON.stringify(persistenceFailure)
    });
    vi.stubGlobal("fetch", fetchMock);
    const first = render(<PeopleWhoCanHelp jobId={7600} />);

    expect(
      await screen.findByText(
        "JobPilot found potential contacts but could not save the results. No additional search will run unless you retry."
      )
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry discovery" })).not.toBeInTheDocument();
    first.unmount();
    clearPeopleCache();
    render(<PeopleWhoCanHelp jobId={7600} />);
    await screen.findByText(/could not save the results/i);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("coalesces repeated explicit persistence Retry clicks into one mutation", async () => {
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
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return discoveryResponse;
      return Promise.resolve({
        ok: true,
        json: async () => persistenceFailure,
        text: async () => JSON.stringify(persistenceFailure)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelpSummary jobId={7600} onViewAll={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "View people" }));
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
      warnings: [
        "Contact discovery has been upgraded. Refresh to check again."
      ],
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => stale,
        text: async () => JSON.stringify(stale)
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => response(),
        text: async () => JSON.stringify(response())
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelpSummary jobId={7506} onViewAll={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    expect(
      await screen.findByText(
        "Contact discovery has been upgraded. Refresh to check again."
      )
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: "Broaden search" })
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh people" }));
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("coalesces repeated stale Refresh people clicks into one mutation", async () => {
    const stale = response({
      status: "stale",
      warnings: [
        "Contact discovery has been upgraded. Refresh to check again."
      ],
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      },
      search_scope: {
        company_scope: "Hiring company only",
        location_filter: "soft",
        parent_company_matches_included: false,
        refresh_eligible: true,
        exact_company_search_completed: false,
        related_company_search_attempted: false,
        broaden_eligible: false,
        broaden_attempted: false
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
        json: async () => stale,
        text: async () => JSON.stringify(stale)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelpSummary jobId={7506} onViewAll={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    const refresh = await screen.findByRole("button", { name: "Refresh people" });
    fireEvent.click(refresh);
    fireEvent.click(refresh);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")
      ).toHaveLength(1);
    });
    resolveDiscovery({
      ok: true,
      json: async () => response(),
      text: async () => JSON.stringify(response())
    } as Response);
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
  });

  it("does not offer Retry for a non-retryable provider account limitation", async () => {
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
    render(<PeopleWhoCanHelpSummary jobId={7508} onViewAll={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    expect(
      await screen.findByText(
        "The configured provider account does not have access to people search."
      )
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry discovery" })).not.toBeInTheDocument();
  });

  it("does not offer Retry when complete-profile access requires a master key", async () => {
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
    render(<PeopleWhoCanHelpSummary jobId={7600} onViewAll={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "View people" }));

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
      "The professional data provider is temporarily unavailable. You can safely retry later."
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
  ])("shows the %s state after explicit expansion", async (_label, payload, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
      text: async () => JSON.stringify(payload)
    }));
    render(<PeopleWhoCanHelpSummary jobId={7} onViewAll={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("shows a retry after an API failure and recovers without eager requests", async () => {
    const initial = response({
      status: "not_started",
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => initial,
        text: async () => JSON.stringify(initial)
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => response(),
        text: async () => JSON.stringify(response())
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<PeopleWhoCanHelpSummary jobId={731} onViewAll={() => undefined} />);
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "View people" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "People recommendations could not be loaded"
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByText("Find recruiters and referral candidates for this job.")
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Find people" }));
    expect(await screen.findByText("Rita Recruiter")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("limits the compact preview to two people per category", async () => {
    const person = (
      recommendationId: number,
      fullName: string,
      category: string
    ) => ({
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
          person(202, "Manager Two", "potential_hiring_manager"),
          person(203, "Manager Three", "potential_hiring_manager")
        ],
        potential_referrers: [
          person(301, "Referrer One", "potential_referrer"),
          person(302, "Referrer Two", "potential_referrer"),
          person(303, "Referrer Three", "potential_referrer")
        ]
      }
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => current,
      text: async () => JSON.stringify(current)
    }));
    render(
      <PeopleWhoCanHelpSummary
        jobId={7800}
        onViewAll={() => undefined}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "View people" }));

    expect(await screen.findByText("Recruiter One")).toBeInTheDocument();
    expect(screen.getByText("Recruiter Two")).toBeInTheDocument();
    expect(screen.getByText("Manager One")).toBeInTheDocument();
    expect(screen.getByText("Manager Two")).toBeInTheDocument();
    expect(screen.getByText("Referrer One")).toBeInTheDocument();
    expect(screen.getByText("Referrer Two")).toBeInTheDocument();
    expect(screen.queryByText("Recruiter Three")).not.toBeInTheDocument();
    expect(screen.queryByText("Manager Three")).not.toBeInTheDocument();
    expect(screen.queryByText("Referrer Three")).not.toBeInTheDocument();
  });
});
