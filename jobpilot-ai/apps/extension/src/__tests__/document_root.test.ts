/**
 * Section C — the frame document as a last-resort application root.
 *
 * The live Airbnb/Greenhouse failure: the application is rendered by React at
 * document level with no <form> and no container matching a known ATS selector.
 * Every candidate scored below threshold, so the run aborted with "JobPilot
 * couldn't identify the application form on this page" — on a page where the
 * application was fully visible.
 *
 * The document root must be reachable, but it must NOT become a global scan:
 * a page whose only form is a search box still has to be rejected.
 */

import { describe, expect, it } from "vitest";
import { resolveApplicationRoot } from "../ats/formRoot";

function mount(html: string): Document {
  document.documentElement.innerHTML = `<head></head><body>${html}</body>`;
  return document;
}

/**
 * A form-less React application whose controls are spread across TOP-LEVEL
 * siblings, so their nearest common ancestor is <body> itself.
 *
 * This is the shape the container heuristics genuinely cannot serve: there is
 * no <form>, no recognisable ATS wrapper, and no single element that contains
 * the application without also being the whole document. It is precisely the
 * case that produced "couldn't identify the application form" live.
 */
const DOCUMENT_LEVEL_APPLICATION = `
  <h1>Software Engineer</h1>
  <div class="jsx-1a2b3c">
    <label for="first_name">First Name *</label>
    <input id="first_name" required />
    <label for="last_name">Last Name *</label>
    <input id="last_name" required />
  </div>
  <div class="jsx-4d5e6f">
    <label for="email">Email *</label>
    <input id="email" type="email" required />
    <label for="phone">Phone</label>
    <input id="phone" type="tel" />
  </div>
  <div class="jsx-7g8h9i">
    <label for="location">Location</label>
    <input id="location" />
    <label for="linkedin">LinkedIn Profile</label>
    <input id="linkedin" />
  </div>
  <div class="jsx-0j1k2l">
    <label for="resume">Resume/CV *</label>
    <input id="resume" type="file" required />
  </div>
  <button type="button">Submit application</button>`;

describe("document-level application root", () => {
  it("accepts the frame document when no container qualifies", () => {
    const doc = mount(DOCUMENT_LEVEL_APPLICATION);
    const result = resolveApplicationRoot(doc);

    expect(result.confident).toBe(true);
    expect(result.rootKind).toBe("document");
    expect(result.root).toBe(doc);
  });

  it("explains in diagnostics why the document was chosen", () => {
    const result = resolveApplicationRoot(mount(DOCUMENT_LEVEL_APPLICATION));
    expect(result.explanation).toMatch(/frame document/i);
    // The explanation must carry the evidence, not just a verdict.
    expect(result.explanation).toMatch(/\d+ controls/);
    expect(result.candidates.some((c) => c.signals.includes("document_fallback"))).toBe(true);
  });

  it("still rejects a page whose only form is a site search", () => {
    const doc = mount(`
      <nav>
        <form role="search">
          <label for="q">Search jobs</label>
          <input id="q" type="search" />
          <button type="submit">Search</button>
        </form>
      </nav>
      <main><p>No application on this page yet.</p></main>`);
    const result = resolveApplicationRoot(doc);

    expect(result.confident).toBe(false);
    expect(result.reason).toBe("NO_APPLICATION_FORM");
  });

  it("explains WHY nothing was chosen, rather than failing silently", () => {
    const result = resolveApplicationRoot(
      mount(`<nav><form role="search"><input id="q" type="search" /></form></nav>`)
    );
    expect(result.explanation).toBeTruthy();
    // Must name the threshold and the evidence considered.
    expect(result.explanation).toMatch(/threshold/i);
    expect(result.explanation).toMatch(/candidate/i);
  });

  it("does not accept the document for a newsletter signup page", () => {
    const doc = mount(`
      <footer>
        <form class="newsletter-signup">
          <label for="nl">Email</label><input id="nl" type="email" />
          <button type="submit">Subscribe</button>
        </form>
      </footer>`);
    expect(resolveApplicationRoot(doc).confident).toBe(false);
  });

  it("does not accept a document carrying only one stray text input", () => {
    const doc = mount(`<main><label for="x">Email</label><input id="x" type="email" /></main>`);
    const result = resolveApplicationRoot(doc);
    expect(result.confident).toBe(false);
    // No application anchor at all — say so.
    expect(result.explanation).toMatch(/anchor|threshold/i);
  });

  it("prefers a real container over the document when one qualifies", () => {
    const doc = mount(`
      <div id="grnhse_app">
        <label for="fn">First Name *</label><input id="fn" required />
        <label for="ln">Last Name *</label><input id="ln" required />
        <label for="em">Email *</label><input id="em" type="email" required />
        <label for="cv">Resume/CV</label><input id="cv" type="file" />
        <button>Submit application</button>
      </div>`);
    const result = resolveApplicationRoot(doc);

    expect(result.confident).toBe(true);
    // The tighter container wins; the document fallback is genuinely last-resort.
    expect(result.rootKind).not.toBe("document");
    expect((result.element as Element).id).toBe("grnhse_app");
  });

  it("ignores JobPilot's own widget when scoring the document", () => {
    const doc = mount(`
      <div id="jobpilot-assisted-apply">
        <label for="w1">First Name</label><input id="w1" />
        <label for="w2">Email</label><input id="w2" />
        <button>Submit</button>
      </div>
      <main><p>Careers landing page</p></main>`);
    expect(resolveApplicationRoot(doc).confident).toBe(false);
  });
});
