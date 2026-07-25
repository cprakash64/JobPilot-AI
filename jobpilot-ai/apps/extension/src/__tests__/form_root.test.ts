/**
 * Section A/K — application-form ROOT scoping.
 *
 * These reproduce the two live failures directly:
 *   • Samsara: the application is React-rendered with NO <form>, while the site
 *     search IS a real <form>. The old "form with the most inputs" rule picked
 *     the search box → "Discovered: 1, Filled: 1".
 *   • MongoDB: the global search input became an application question, and an
 *     unrelated marketing checkbox group surfaced as "This question".
 */
import { describe, expect, it } from "vitest";
import { resolveApplicationRoot } from "../ats/formRoot";
import { discoverAll } from "../fields/discovery";
import { MONGODB_LIVE_SHAPE_FIXTURE, SAMSARA_LIVE_SHAPE_FIXTURE, mountFixture } from "./fixtures";

function labelsIn(root: ParentNode): string[] {
  return discoverAll(root).fields.map((f) => f.label || f.ariaLabel || f.placeholder || f.name);
}

describe("application-form root resolution", () => {
  it("Samsara: selects the form-less React application, NOT the site search form", () => {
    mountFixture(SAMSARA_LIVE_SHAPE_FIXTURE);
    const result = resolveApplicationRoot(document);

    expect(result.confident).toBe(true);
    expect((result.element as HTMLElement).id).toBe("application");

    // The regression that produced "Discovered: 1": every real field is found.
    const fields = discoverAll(result.root!).fields;
    expect(fields.length).toBeGreaterThanOrEqual(8);
    const ids = fields.map((f) => f.id);
    for (const id of ["first_name", "last_name", "email", "phone", "city", "zip", "resume", "country"]) {
      expect(ids, `${id} must be discovered`).toContain(id);
    }
    // And the search box is NOT among them.
    expect(ids).not.toContain("site-search");
  });

  it("Samsara: the search form and the newsletter form are excluded with reasons", () => {
    mountFixture(SAMSARA_LIVE_SHAPE_FIXTURE);
    const { candidates } = resolveApplicationRoot(document);
    const excluded = candidates.filter((c) => c.excluded);
    expect(excluded.length).toBeGreaterThanOrEqual(2);
    expect(excluded.map((c) => c.excluded).join(" ")).toMatch(/site_chrome|search|newsletter|subscribe/);
  });

  it("MongoDB: the global site search never becomes an application question", () => {
    mountFixture(MONGODB_LIVE_SHAPE_FIXTURE);
    const result = resolveApplicationRoot(document);

    expect(result.confident).toBe(true);
    expect((result.element as HTMLElement).id).toBe("careers-application");

    const labels = labelsIn(result.root!).join(" | ");
    expect(labels).not.toMatch(/Search products, whitepapers/i);
    // The unrelated marketing checkbox group is outside the root too.
    expect(labels).not.toMatch(/Which topics interest you/i);
    // The genuine application fields ARE present.
    expect(labels).toMatch(/First Name/i);
    expect(labels).toMatch(/previously worked at MongoDB/i);
  });

  it("scores the resume upload and submit control as the strongest evidence", () => {
    mountFixture(SAMSARA_LIVE_SHAPE_FIXTURE);
    const { candidates } = resolveApplicationRoot(document);
    const winner = candidates.find((c) => !c.excluded && c.score > 0)!;
    expect(winner.signals).toEqual(expect.arrayContaining(["resume_upload", "submit_control"]));
  });

  it("refuses to guess when no candidate is confidently an application form", () => {
    mountFixture(`
      <header><form role="search"><input type="search" name="q" /></form></header>
      <main><section><p>Marketing page with no application.</p>
        <form class="newsletter"><input type="email" name="newsletter_email" /></form>
      </section></main>`);
    const result = resolveApplicationRoot(document);
    expect(result.confident).toBe(false);
    expect(result.root).toBeNull();
    expect(result.reason).toBe("NO_APPLICATION_FORM");
  });

  it("never mixes fields from two unrelated candidate forms", () => {
    // Two sibling application-shaped forms → ambiguous, so neither is used.
    mountFixture(`
      <main>
        <form id="app-a" action="/apply">
          <label for="a1">First Name</label><input id="a1" name="firstName" required />
          <label for="a2">Email</label><input id="a2" type="email" name="email" required />
          <label for="a3">Resume/CV</label><input id="a3" type="file" name="resume" required />
          <button type="submit">Submit Application</button>
        </form>
        <form id="app-b" action="/apply">
          <label for="b1">First Name</label><input id="b1" name="firstName" required />
          <label for="b2">Email</label><input id="b2" type="email" name="email" required />
          <label for="b3">Resume/CV</label><input id="b3" type="file" name="resume" required />
          <button type="submit">Submit Application</button>
        </form>
      </main>`);
    const result = resolveApplicationRoot(document);
    expect(result.confident).toBe(false);
    expect(result.reason).toBe("APPLICATION_FORM_AMBIGUOUS");
    expect(result.root).toBeNull();
    expect(result.candidates.length).toBeGreaterThanOrEqual(2);
  });

  it("JobPilot's own widget is never a candidate", () => {
    mountFixture(SAMSARA_LIVE_SHAPE_FIXTURE);
    const widget = document.createElement("div");
    widget.id = "jobpilot-assisted-apply";
    widget.innerHTML = `<form><input name="firstName" required /><input type="email" name="email" required />
                        <input type="file" name="resume" /><button type="submit">Submit Application</button></form>`;
    document.body.appendChild(widget);

    const result = resolveApplicationRoot(document);
    expect((result.element as HTMLElement)?.id).toBe("application");
    expect(result.candidates.some((c) => c.excluded === "jobpilot_widget")).toBe(true);
  });
});
