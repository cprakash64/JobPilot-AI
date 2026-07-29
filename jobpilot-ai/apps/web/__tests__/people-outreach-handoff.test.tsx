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

async function openDraft(label = "Create LinkedIn draft") {
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

  it("renders managers with a Create LinkedIn draft action", async () => {
    installTransport();
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    expect(await screen.findByText("Morgan Manager")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Create LinkedIn draft" }).length
    ).toBe(2);
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

  it("opens exactly the validated contact URL in a new tab", async () => {
    installTransport();
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const dialog = await openDraft();

    fireEvent.click(within(dialog).getByRole("button", { name: /Open LinkedIn/ }));
    expect(open).toHaveBeenCalledWith(LINKEDIN_URL, "_blank", "noopener,noreferrer");
  });

  it("disables Open LinkedIn and explains why when no URL exists", async () => {
    installTransport(draftResponse({ linkedin_url: null, linkedin_available: false }));
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    // The second manager has no profile URL at all.
    const buttons = await screen.findAllByRole("button", { name: "Create LinkedIn draft" });
    fireEvent.click(buttons[1]);
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("button", { name: /Open LinkedIn/ })).toBeDisabled();
    expect(
      within(dialog).getByText(/LinkedIn profile URL is unavailable for this contact/)
    ).toBeInTheDocument();
    // Copying still works, so the draft is not wasted.
    expect(within(dialog).getByRole("button", { name: /Copy message/ })).toBeEnabled();
  });

  it("never opens a window when the URL is missing", async () => {
    installTransport(draftResponse({ linkedin_url: null, linkedin_available: false }));
    const open = vi.fn();
    vi.stubGlobal("open", open);
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    const buttons = await screen.findAllByRole("button", { name: "Create LinkedIn draft" });
    fireEvent.click(buttons[1]);
    const dialog = await screen.findByRole("dialog");

    fireEvent.click(within(dialog).getByRole("button", { name: /Open LinkedIn/ }));
    expect(open).not.toHaveBeenCalled();
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

describe("Email draft workflow", () => {
  afterEach(() => {
    cleanup();
    clearPeopleCache();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const emailDraft = draftResponse({
    message_type: "email",
    subject: "Question about Software Engineer at L3Harris Technologies",
    body: "Hi Morgan,\n\nI’m reaching out about the Software Engineer role.\n\nThanks\nSam",
    professional_email: "morgan@l3harris.example",
    email_available: true,
    linkedin_url: LINKEDIN_URL,
    linkedin_available: true,
    character_count: 74
  });

  it("shows an editable subject and body", async () => {
    installTransport(emailDraft);
    const dialog = await openDraft("Create email draft");

    const subject = within(dialog).getByLabelText("Outreach subject") as HTMLInputElement;
    const body = within(dialog).getByLabelText("Outreach draft") as HTMLTextAreaElement;
    expect(subject.value).toBe("Question about Software Engineer at L3Harris Technologies");
    expect(body.value).toContain("I’m reaching out");
    expect(subject.value).not.toBe(body.value);

    fireEvent.change(subject, { target: { value: "Revised subject" } });
    expect((within(dialog).getByLabelText("Outreach subject") as HTMLInputElement).value).toBe(
      "Revised subject"
    );
  });

  it("copies the subject separately from the message", async () => {
    installTransport(emailDraft);
    const writeText = stubClipboard();
    const dialog = await openDraft("Create email draft");

    fireEvent.click(within(dialog).getByRole("button", { name: /Copy subject/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    expect(writeText.mock.calls[0][0]).toBe(
      "Question about Software Engineer at L3Harris Technologies"
    );
  });

  it("opens a correctly encoded mailto URL", async () => {
    installTransport(emailDraft);
    const dialog = await openDraft("Create email draft");
    const location = { href: "" } as Location;
    vi.stubGlobal("location", location);

    fireEvent.click(within(dialog).getByRole("button", { name: /Open email app/ }));

    expect(location.href.startsWith("mailto:morgan@l3harris.example?")).toBe(true);
    const params = new URLSearchParams(new URL(location.href).search);
    expect(params.get("subject")).toBe(
      "Question about Software Engineer at L3Harris Technologies"
    );
    expect(params.get("body")).toContain("I’m reaching out about the Software Engineer role.");
    // Newlines survive as encoded characters rather than breaking the URL.
    expect(location.href).toContain("%0A");
  });

  it("disables Open email app and explains why without a verified address", async () => {
    installTransport(
      draftResponse({
        message_type: "email",
        subject: "Question about Software Engineer",
        professional_email: null,
        email_available: false
      })
    );
    const dialog = await openDraft("Create email draft");

    expect(within(dialog).getByRole("button", { name: /Open email app/ })).toBeDisabled();
    expect(within(dialog).getByText(/Verified email unavailable/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Copy message/ })).toBeEnabled();
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
    const buttons = await screen.findAllByRole("button", { name: "Create LinkedIn draft" });
    fireEvent.click(buttons[0]);

    expect(
      await screen.findByText(/current employment needs to be re-checked/i)
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps Save contact and Mark contacted working", async () => {
    const fetchMock = installTransport();
    render(<PeopleWhoCanHelp jobId={JOB_ID} />);
    await screen.findByText("Morgan Manager");

    fireEvent.click(screen.getAllByRole("button", { name: /Save contact/ })[0]);
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).endsWith("/save"))
      ).toBe(true);
    });

    fireEvent.click(screen.getAllByRole("button", { name: /Mark contacted/ })[0]);
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).endsWith("/contacted"))
      ).toBe(true);
    });
  });
});
