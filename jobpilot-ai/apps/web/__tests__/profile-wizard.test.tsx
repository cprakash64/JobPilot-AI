import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileWizard } from "../components/ProfileWizard";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

function mockProfileFetch(existingProfile: Record<string, unknown> | null = null, options: { applyStatus?: number } = {}) {
  const requests: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    requests.push({ url: String(input), init });
    const url = String(input);
    if (url.endsWith("/profile/import") || url.endsWith("/profile/import/text") || url.endsWith("/profile/import/file")) {
      return Promise.resolve(
        jsonResponse({
          draft: {
            basic_info: { full_name: "Imported Candidate", email: "candidate@example.com", phone: "555-111-2222" },
            job_targets: { target_roles: ["Backend Engineer"], work_preference: "remote" },
            education: [],
            experience: [
              {
                company: "Acme",
                title: "Backend Engineer Intern",
                bullets: ["Built APIs for job search tools."],
                technologies: ["Python"]
              }
            ],
            projects: [{ name: "JobPilot AI", bullets: ["Built import preview"] }],
            skills: ["Python", "FastAPI"],
            certifications: [],
            awards: [],
            links: { linkedin_url: "https://www.linkedin.com/in/imported" },
            confidence_warnings: [
              "AI parsing is unavailable. We extracted basic fields using a simple parser.",
              "AI parsing is unavailable. We extracted basic fields using a simple parser."
            ],
            raw_text_preview: "Imported Candidate\nSkills: Python, FastAPI",
            source_type: url.endsWith("/profile/import/file") ? "resume" : "resume_text"
          }
        })
      );
    }
    if (url.endsWith("/profile/import/apply")) {
      if (options.applyStatus) {
        return Promise.resolve(jsonResponse({ detail: "Could not save imported profile." }, options.applyStatus));
      }
      const body = JSON.parse(String(init?.body ?? "{}"));
      const draft = body.draft ?? {};
      return Promise.resolve(
        jsonResponse({
          profile: {
            ...(existingProfile ?? {}),
            full_name: draft.basic_info?.full_name ?? "Imported Candidate",
            phone: draft.basic_info?.phone ?? "",
            location_country: draft.basic_info?.location_country ?? "United States",
            target_roles: draft.job_targets?.target_roles ?? [],
            target_levels: draft.job_targets?.target_levels ?? [],
            preferred_locations: draft.job_targets?.preferred_locations ?? [],
            work_preference: draft.job_targets?.work_preference ?? "everything",
            skills: draft.skills ?? [],
            linkedin_url: draft.links?.linkedin_url ?? ""
          },
          career: {
            education: draft.education ?? [],
            experience: draft.experience ?? [],
            projects: draft.projects ?? [],
            certifications: draft.certifications ?? [],
            awards: draft.awards ?? []
          }
        })
      );
    }
    if (url.endsWith("/profile/career") && init?.method === "PUT") {
      return Promise.resolve(jsonResponse({ education: [], experience: [], projects: [] }));
    }
    if (url.endsWith("/profile") && init?.method === "PUT") {
      return Promise.resolve(jsonResponse({ profile: JSON.parse(String(init.body)) }));
    }
    if (url.endsWith("/profile/career")) {
      return Promise.resolve(jsonResponse({ education: [], experience: [], projects: [], certifications: [], awards: [] }));
    }
    if (url.endsWith("/profile")) {
      return Promise.resolve(jsonResponse({ profile: existingProfile }));
    }
    return Promise.resolve(jsonResponse({}));
  });
  return requests;
}

describe("ProfileWizard", () => {
  beforeEach(() => {
    cleanup();
    localStorage.setItem("jobpilot_token", "token");
    vi.restoreAllMocks();
  });

  it("renders work authorization dropdown and preference defaults", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(screen.getByRole("button", { name: "2. Basic info" }));
    expect(await screen.findByLabelText("Work authorization")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Authorized to work in the United States" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "3. Job targets" }));
    expect(screen.getByLabelText("Preference")).toHaveValue("everything");
  });

  it("supports target role, level, and location chips", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(screen.getByRole("button", { name: "3. Job targets" }));
    await userEvent.click(await screen.findByRole("button", { name: "Software Engineer" }));
    await userEvent.click(screen.getByRole("button", { name: "New Grad" }));
    await userEvent.click(screen.getByRole("button", { name: "Remote" }));

    expect(screen.getAllByText("Software Engineer").length).toBeGreaterThan(1);
    expect(screen.getAllByText("New Grad").length).toBeGreaterThan(1);
    await waitFor(() => expect(screen.getByLabelText("Remove Remote")).toBeInTheDocument());
  });

  it("opens paste import modal and accepts imported draft", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(await screen.findByRole("button", { name: "Paste profile text" }));
    await userEvent.type(
      screen.getByPlaceholderText("Paste resume text, LinkedIn Save to PDF text, or profile notes."),
      "Imported Candidate\nSkills: Python, FastAPI\nBackend Engineer"
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract draft profile" }));
    await userEvent.click(await screen.findByRole("button", { name: "Accept all" }));

    expect(await screen.findByText("Imported profile saved successfully.")).toBeInTheDocument();
    expect(screen.getByText(/Imported Candidate/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/profile/import/apply",
      expect.objectContaining({ method: "POST", body: expect.stringContaining("Imported Candidate") })
    );
  });

  it("shows a resume-style preview and dedupes import warnings", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(await screen.findByRole("button", { name: "Paste profile text" }));
    await userEvent.type(
      screen.getByPlaceholderText("Paste resume text, LinkedIn Save to PDF text, or profile notes."),
      "Imported Candidate\nSkills: Python, FastAPI\nBackend Engineer"
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract draft profile" }));

    // Default mode is the read-only resume document, not a raw field form.
    expect(await screen.findByRole("heading", { name: "Review imported profile" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Imported Candidate" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Professional Experience" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Full name")).not.toBeInTheDocument();
    expect(screen.getAllByText("AI parsing is unavailable. We extracted basic fields using a simple parser.")).toHaveLength(1);

    // Clicking Edit reveals the editable header fields.
    await userEvent.click(screen.getByRole("button", { name: "Edit Header" }));
    expect(screen.getByLabelText("Full name")).toHaveValue("Imported Candidate");
  });

  it("empty imported sections are hidden from the resume by default", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(await screen.findByRole("button", { name: "Paste profile text" }));
    await userEvent.type(
      screen.getByPlaceholderText("Paste resume text, LinkedIn Save to PDF text, or profile notes."),
      "Imported Candidate\nSkills: Python, FastAPI\nBackend Engineer"
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract draft profile" }));

    await screen.findByRole("heading", { name: "Review imported profile" });
    // The mock draft has no awards/certifications, so those resume sections do
    // not clutter the document.
    expect(screen.queryByRole("heading", { name: "Awards, Publications & Recognition" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Certifications" })).not.toBeInTheDocument();
  });

  it("sends edited preview values to the apply endpoint", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(await screen.findByRole("button", { name: "Paste profile text" }));
    await userEvent.type(
      screen.getByPlaceholderText("Paste resume text, LinkedIn Save to PDF text, or profile notes."),
      "Imported Candidate\nSkills: Python, FastAPI\nBackend Engineer"
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract draft profile" }));

    await userEvent.click(await screen.findByRole("button", { name: "Edit Header" }));
    await userEvent.clear(screen.getByLabelText("Full name"));
    await userEvent.type(screen.getByLabelText("Full name"), "Edited Candidate");

    await userEvent.click(screen.getByRole("button", { name: "Edit Professional Experience" }));
    await userEvent.clear(screen.getByLabelText("Experience 1 bullets"));
    await userEvent.type(screen.getByLabelText("Experience 1 bullets"), "Edited-production-API-bullet");
    await userEvent.click(screen.getByRole("button", { name: "Accept all" }));

    await waitFor(() => expect(screen.getByText("Imported profile saved successfully.")).toBeInTheDocument());
    const applyCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith("/profile/import/apply"));
    const body = JSON.parse(String(applyCall?.[1]?.body));
    expect(body.draft.basic_info.full_name).toBe("Edited Candidate");
    expect(body.draft.experience[0].bullets).toEqual(["Edited-production-API-bullet"]);
  });

  it("apply selected sends only checked sections", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(await screen.findByRole("button", { name: "Paste profile text" }));
    await userEvent.type(
      screen.getByPlaceholderText("Paste resume text, LinkedIn Save to PDF text, or profile notes."),
      "Imported Candidate\nSkills: Python, FastAPI\nBackend Engineer"
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract draft profile" }));

    // Uncheck every section chip except Skills.
    for (const label of ["Header", "Experience", "Projects", "Education", "Awards", "Certifications", "Targets", "Links"]) {
      await userEvent.click(await screen.findByLabelText(label));
    }
    await userEvent.click(screen.getByRole("button", { name: "Apply selected" }));

    await waitFor(() => expect(screen.getByText("Selected imported sections saved successfully.")).toBeInTheDocument());
    const applyCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith("/profile/import/apply"));
    const body = JSON.parse(String(applyCall?.[1]?.body));
    expect(body.sections).toEqual(["skills"]);
  });

  it("shows import apply API errors inline and keeps the modal open", async () => {
    mockProfileFetch(null, { applyStatus: 400 });
    render(React.createElement(ProfileWizard));

    await userEvent.click(await screen.findByRole("button", { name: "Paste profile text" }));
    await userEvent.type(
      screen.getByPlaceholderText("Paste resume text, LinkedIn Save to PDF text, or profile notes."),
      "Imported Candidate\nSkills: Python, FastAPI\nBackend Engineer"
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract draft profile" }));
    await userEvent.click(await screen.findByRole("button", { name: "Accept all" }));

    expect(await screen.findByText("Could not save imported profile.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Review imported profile" })).toBeInTheDocument();
  });

  it("upload resume accepts PDF and DOCX files", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    const resumeInput = await screen.findByLabelText("Upload resume");
    expect(resumeInput).toHaveAttribute("accept", ".pdf,.docx");
    await userEvent.upload(resumeInput, new File(["resume"], "resume.pdf", { type: "application/pdf" }));

    expect(await screen.findByText("Warnings")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/profile/import/file",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) })
    );
  });

  it("upload LinkedIn PDF accepts PDF only", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    const linkedInInput = await screen.findByLabelText("Upload LinkedIn PDF");
    expect(linkedInInput).toHaveAttribute("accept", ".pdf");
    await userEvent.upload(linkedInInput, new File(["linkedin"], "linkedin.pdf", { type: "application/pdf" }));

    expect(await screen.findByRole("button", { name: "Show raw extracted text" })).toBeInTheDocument();
  });

  it("shows existing data conflicts before applying an import", async () => {
    mockProfileFetch({ full_name: "Existing Candidate", skills: [], target_roles: [] });
    render(React.createElement(ProfileWizard));

    await userEvent.click(await screen.findByRole("button", { name: "Paste profile text" }));
    await userEvent.type(
      screen.getByPlaceholderText("Paste resume text, LinkedIn Save to PDF text, or profile notes."),
      "Imported Candidate\nSkills: Python, FastAPI\nBackend Engineer"
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract draft profile" }));

    expect(await screen.findByText("Existing data conflicts")).toBeInTheDocument();
    expect(screen.getByText("Existing: Existing Candidate")).toBeInTheDocument();
    expect(screen.getByText("Imported: Imported Candidate")).toBeInTheDocument();
  });

  it("persists profile data after save", async () => {
    mockProfileFetch();
    render(React.createElement(ProfileWizard));

    await userEvent.click(screen.getByRole("button", { name: "2. Basic info" }));
    await userEvent.type(await screen.findByLabelText("Full name *"), "Test Candidate");
    await userEvent.click(screen.getByRole("button", { name: "3. Job targets" }));
    await userEvent.click(await screen.findByRole("button", { name: "Backend Engineer" }));
    await userEvent.selectOptions(screen.getByLabelText("Preference"), "remote");
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => expect(screen.getByText("Profile saved.")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/profile",
      expect.objectContaining({
        method: "PUT",
        body: expect.stringContaining("Backend Engineer")
      })
    );
  });
});
