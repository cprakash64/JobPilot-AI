import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PeopleWhoCanHelp } from "../components/PeopleWhoCanHelp";
import { clearPeopleCache } from "../lib/peopleClient";
import {
  buildMailtoUrl,
  safeEmailAddress,
  safeLinkedInUrl
} from "../lib/outreachHandoff";

/**
 * JobPilot never sends a message. Clicking a channel action produces an
 * editable draft; the user reviews it and hands off to LinkedIn or their mail
 * client themselves. These tests cover the L3Harris scenario where valid
 * managers rendered but every draft attempt failed.
 */

const LINKEDIN_URL = "https://www.linkedin.com/in/morgan-manager";
const JOB_ID = 4200;

const manager = {
  recommendation_id: 91,
  full_name: "Morgan Manager",
  current_title: "Engineering Manager",
  current_company: "L3Harris Technologies",
  category: "potential_hiring_manager",
  category_label: "Potential hiring manager",
  relevance_score: 84,
  confidence: "high",
  current_employment_confidence: 0.78,
  employment_validation_status: "exact_company_current_but_unverified_freshness",
  employment_last_verified_at: "2026-07-25T12:00:00Z",
  employment_warning: null,
  email_lookup_allowed: true,
  reasons: ["Currently listed at the hiring company."],
  limitations: [],
  last_checked_at: "2026-07-25T12:00:00Z",
  professional_profile_url: LINKEDIN_URL,
  email_status: "not_requested",
  professional_email: null,
  email_verified_at: null,
  saved: false,
  contacted: false
};

const secondManager = {
  ...manager,
  recommendation_id: 92,
  full_name: "Riley Lead",
  professional_profile_url: null
};

function peopleResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "complete",
    availability_reason: "available",
    result_freshness: "fresh",
    beta: true,
    warnings: [],
    generated_at: "2026-07-28T12:00:00Z",
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
    categories: {
      likely_recruiters: [],
      potential_hiring_managers: [manager, secondManager],
      potential_referrers: []
    },
    controls: { email_discovery: true, outreach_drafting: true },
    ...overrides
  };
}

function draftResponse(overrides: Record<string, unknown> = {}) {
  return {
    message_type: "linkedin_message",
    subject: null,
    body: "Hi Morgan, I’m applying for the Software Engineer role at L3Harris Technologies.",
    facts_used: ["job:Software Engineer", "company:L3Harris Technologies"],
    assumptions: [],
    omitted_uncertain_facts: ["team_membership_unconfirmed"],
    character_count: 79,
    requires_manual_review: true,
    generation_path: "deterministic_template",
    template_version: "people-outreach-template-v2",
    recipient_name: "Morgan Manager",
    recipient_category: "potential_hiring_manager",
    linkedin_url: LINKEDIN_URL,
    linkedin_available: true,
    professional_email: null,
    email_available: false,
    sent: false,
    ...overrides
  };
}

function installTransport(draft: Record<string, unknown> = draftResponse()) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const payload = url.includes("/outreach-draft") ? draft : peopleResponse();
    return Promise.resolve({
      ok: true,
      json: async () => payload,
      text: async () => JSON.stringify(payload)
    } as Response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { ...globalThis.navigator, clipboard: { writeText } });
  return writeText;
}

async function openDraft(label = "Draft message") {
  render(<PeopleWhoCanHelp jobId={JOB_ID} />);
  const buttons = await screen.findAllByRole("button", { name: label });
  fireEvent.click(buttons[0]);
  return await screen.findByRole("dialog");
}

describe("outreach handoff helpers", () => {
  it("accepts a real LinkedIn profile URL", () => {
    expect(safeLinkedInUrl(LINKEDIN_URL)).toBe(LINKEDIN_URL);
    expect(safeLinkedInUrl("https://uk.linkedin.com/in/someone")).toBe(
      "https://uk.linkedin.com/in/someone"
    );
  });

  it.each([
    ["javascript:alert(1)"],
    ["data:text/html,<script>"],
    ["http://www.linkedin.com/in/morgan-manager"],
    ["https://phishing.example/in/morgan-manager"],
    ["https://www.linkedin.com/company/l3harris"],
    ["https://user:pass@www.linkedin.com/in/morgan"],
    [""],
    [null]
  ])("rejects unsafe or non-profile value %s", (value) => {
    expect(safeLinkedInUrl(value as string | null)).toBeNull();
  });

  it("never derives a LinkedIn URL from a name", () => {
    // There is no code path that builds a URL; absence yields null.
    expect(safeLinkedInUrl(undefined)).toBeNull();
  });

  it("validates email addresses structurally", () => {
    expect(safeEmailAddress("morgan@l3harris.example")).toBe("morgan@l3harris.example");
    expect(safeEmailAddress("not-an-email")).toBeNull();
    expect(safeEmailAddress("two@@at.example")).toBeNull();
    expect(safeEmailAddress("with space@x.example")).toBeNull();
    expect(safeEmailAddress(null)).toBeNull();
  });

  it("encodes every reserved character in a mailto URL", () => {
    const url = buildMailtoUrl({
      address: "morgan@l3harris.example",
      subject: "Question about R&D role?",
      body: "Hi Morgan,\n\nQ&A: is this role open?\n\nThanks"
    });
    expect(url.startsWith("mailto:morgan@l3harris.example?")).toBe(true);
    // Raw separators would truncate the body at the first one.
    const query = url.split("?")[1];
    expect(query).not.toMatch(/[\n]/);
    expect(query.split("&").filter((part) => part.startsWith("subject=")).length).toBe(1);
    expect(url).toContain("R%26D");
    expect(url).toContain("%0A");
    const parsed = new URL(url);
    const params = new URLSearchParams(parsed.search);
    expect(params.get("subject")).toBe("Question about R&D role?");
    expect(params.get("body")).toContain("Q&A: is this role open?");
  });

  it("omits absent parts rather than emitting empty parameters", () => {
    expect(buildMailtoUrl({ address: "a@b.example" })).toBe("mailto:a@b.example");
  });
});

describe("LinkedIn draft workflow", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("offers one draft action per contact and no bookkeeping buttons", async () => {
    installTransport();
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    expect(await screen.findByText("Morgan Manager")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Draft message" }).length).toBe(2);
    // Save contact / Mark contacted were removed from the card.
    expect(screen.queryByRole("button", { name: /Save contact/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mark contacted/ })).not.toBeInTheDocument();
  });

  it("links straight to a verified LinkedIn profile from the card", async () => {
    installTransport();
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    await screen.findByText("Morgan Manager");

    const link = screen.getByRole("link", { name: /LinkedIn/ });
    expect(link).toHaveAttribute("href", LINKEDIN_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // The contact without a verified URL gets an honest disabled affordance
    // instead of a guessed link.
    expect(screen.getAllByRole("link", { name: /LinkedIn/ })).toHaveLength(1);
    expect(screen.getByTitle(/never guesses a profile URL/)).toBeInTheDocument();
  });

  it("opens an editable preview instead of showing a red failure", async () => {
    installTransport();
    const dialog = await openDraft();

    expect(within(dialog).getByText("LinkedIn message to Morgan Manager")).toBeInTheDocument();
    const body = within(dialog).getByLabelText("Outreach draft") as HTMLTextAreaElement;
    expect(body.value).toContain("Hi Morgan");
    expect(within(dialog).getByText(/79 characters/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("notes when the draft came from a verified template", async () => {
    installTransport();
    const dialog = await openDraft();
    expect(
      within(dialog).getByText("Generated from a verified template.")
    ).toBeInTheDocument();
  });

  it("lets the user edit the draft and updates the character count", async () => {
    installTransport();
    const dialog = await openDraft();
    const body = within(dialog).getByLabelText("Outreach draft");
    fireEvent.change(body, { target: { value: "Short note." } });
    expect(within(dialog).getByText("11 characters")).toBeInTheDocument();
  });

  it("copies the message to the clipboard", async () => {
    installTransport();
    const writeText = stubClipboard();
    const dialog = await openDraft();

    fireEvent.click(within(dialog).getByRole("button", { name: /Copy message/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    expect(writeText.mock.calls[0][0]).toContain("Hi Morgan");
    expect(await within(dialog).findByRole("button", { name: /Message copied/ })).toBeInTheDocument();
  });

  it("copies the draft and opens exactly the validated profile URL", async () => {
    // LinkedIn accepts no prefilled message in a profile URL, so the honest
    // handoff copies the text and opens the real profile in a new tab.
    installTransport();
    const writeText = stubClipboard();
    const dialog = await openDraft();

    const link = within(dialog).getByRole("link", { name: /Copy and open LinkedIn/ });
    expect(link).toHaveAttribute("href", LINKEDIN_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(
      within(dialog).getByText(/LinkedIn does not accept a prefilled message/)
    ).toBeInTheDocument();

    fireEvent.click(link);
    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    expect(writeText.mock.calls[0][0]).toContain("Hi Morgan");
  });

  it("disables Open LinkedIn and explains why when no URL exists", async () => {
    installTransport(draftResponse({ linkedin_url: null, linkedin_available: false }));
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    // The second manager has no profile URL at all.
    const buttons = await screen.findAllByRole("button", { name: "Draft message" });
    fireEvent.click(buttons[1]);
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("button", { name: /Open LinkedIn/ })).toBeDisabled();
    expect(
      within(dialog).getByText(/LinkedIn profile URL is unavailable for this contact/)
    ).toBeInTheDocument();
    // Copying still works, so the draft is not wasted.
    expect(within(dialog).getByRole("button", { name: /Copy message/ })).toBeEnabled();
  });

  it("offers no link at all when the URL is missing", async () => {
    installTransport(draftResponse({ linkedin_url: null, linkedin_available: false }));
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    const buttons = await screen.findAllByRole("button", { name: "Draft message" });
    fireEvent.click(buttons[1]);
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).queryByRole("link", { name: /LinkedIn/ })).not.toBeInTheDocument();
  });

  it("closes without sending anything", async () => {
    const fetchMock = installTransport();
    const dialog = await openDraft();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const sendCalls = fetchMock.mock.calls.filter(([input]) =>
      /send|message\/send/.test(String(input))
    );
    expect(sendCalls).toHaveLength(0);
  });

  it("regenerates on request", async () => {
    const fetchMock = installTransport();
    const dialog = await openDraft();
    fireEvent.click(within(dialog).getByRole("button", { name: "Regenerate draft" }));

    await waitFor(() => {
      const draftCalls = fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/outreach-draft")
      );
      expect(draftCalls.length).toBe(2);
    });
  });
});

describe("Email handoff from the contact card", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const verifiedManager = {
    ...manager,
    email_status: "verified",
    professional_email: "morgan@l3harris.example",
    email_verified_at: "2026-07-26T12:00:00Z"
  };

  const emailDraft = draftResponse({
    message_type: "email",
    subject: "Question about Software Engineer at L3Harris Technologies",
    body: "Hi Morgan,\n\nI’m reaching out about the Software Engineer role.\n\nThanks\nSam",
    professional_email: "morgan@l3harris.example",
    email_available: true,
    character_count: 74
  });

  function installVerified(draft: Record<string, unknown> = emailDraft) {
    const payload = peopleResponse({
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [verifiedManager, secondManager],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const body = String(input).includes("/outreach-draft") ? draft : payload;
      return Promise.resolve({
        ok: true,
        json: async () => body,
        text: async () => JSON.stringify(body)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("hands a grounded draft to the mail client with subject and body prefilled", async () => {
    const fetchMock = installVerified();
    const location = { href: "" } as Location;
    vi.stubGlobal("location", location);
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);

    fireEvent.click(await screen.findByRole("button", { name: "Email" }));

    await waitFor(() => expect(location.href.startsWith("mailto:morgan@l3harris.example?")).toBe(true));
    const params = new URLSearchParams(new URL(location.href).search);
    expect(params.get("subject")).toBe(
      "Question about Software Engineer at L3Harris Technologies"
    );
    expect(params.get("body")).toContain("I’m reaching out about the Software Engineer role.");
    // Newlines survive as encoded characters rather than breaking the URL.
    expect(location.href).toContain("%0A");
    // Exactly one draft request per click, and the address came from the
    // backend's verified field rather than a guess.
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).includes("/outreach-draft"))
    ).toHaveLength(1);
    expect(await screen.findByText(/Review it before sending/)).toBeInTheDocument();
  });

  it("shows the verified address and never invents one", async () => {
    installVerified();
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);

    expect(await screen.findByText(/Verified work email: morgan@l3harris.example/)).toBeInTheDocument();
    // The second contact has no verified address, so it gets a lookup action
    // rather than a mailto built from a name and a domain.
    expect(screen.getAllByRole("button", { name: "Email" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /Find work email/ })).toHaveLength(1);
  });

  it("degrades to a disabled affordance when no address can be looked up", async () => {
    const payload = peopleResponse({
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [
          { ...manager, email_lookup_allowed: false, email_status: "not_found" }
        ],
        potential_referrers: []
      }
    });
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
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    await screen.findByText("Morgan Manager");

    expect(screen.queryByRole("button", { name: "Email" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Find work email/ })).not.toBeInTheDocument();
    expect(screen.getByTitle("No work email was found.")).toBeInTheDocument();
  });

  it("still opens the mail client when drafting is switched off", async () => {
    const payload = peopleResponse({
      controls: { email_discovery: true, outreach_drafting: false },
      categories: {
        likely_recruiters: [],
        potential_hiring_managers: [verifiedManager],
        potential_referrers: []
      }
    });
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        json: async () => payload,
        text: async () => JSON.stringify(payload)
      } as Response)
    );
    vi.stubGlobal("fetch", fetchMock);
    const location = { href: "" } as Location;
    vi.stubGlobal("location", location);
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);

    fireEvent.click(await screen.findByRole("button", { name: "Email" }));

    await waitFor(() => expect(location.href).toBe("mailto:morgan@l3harris.example"));
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).includes("/outreach-draft"))
    ).toHaveLength(0);
  });
});

describe("Outreach failure states", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows a specific message when employment must be revalidated", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/outreach-draft")) {
        const body = JSON.stringify({
          detail: {
            code: "PEOPLE_EMPLOYMENT_REVALIDATION_REQUIRED",
            message: "Current employment must be revalidated before drafting outreach."
          }
        });
        return Promise.resolve({
          ok: false,
          status: 409,
          headers: new Headers(),
          json: async () => JSON.parse(body),
          text: async () => body
        } as Response);
      }
      const payload = peopleResponse();
      return Promise.resolve({
        ok: true,
        json: async () => payload,
        text: async () => JSON.stringify(payload)
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    const buttons = await screen.findAllByRole("button", { name: "Draft message" });
    fireEvent.click(buttons[0]);

    expect(
      await screen.findByText(/current employment needs to be re-checked/i)
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("no longer exposes contact bookkeeping actions", async () => {
    const fetchMock = installTransport();
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    await screen.findByText("Morgan Manager");

    // The card is for reaching people, not for tracking them. Neither endpoint
    // is reachable from this surface any more.
    expect(screen.queryByRole("button", { name: /Save contact/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mark contacted/ })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => /\/(save|contacted)$/.test(String(input)))
    ).toBe(false);
  });
});
