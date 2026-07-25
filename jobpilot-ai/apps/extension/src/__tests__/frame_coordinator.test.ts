/**
 * Section B — cross-frame application discovery.
 *
 * The live failure: the application lives in a cross-origin iframe. Every frame
 * ran discovery independently, but only the TOP frame owned the widget, so the
 * top frame — which legitimately has no application in its own document —
 * declared a tab-level failure without ever learning that a sibling frame held
 * the entire application.
 *
 * The coordinator must rank frames on evidence and let a resolved iframe beat an
 * unresolved top frame.
 */

import { describe, expect, it } from "vitest";
import { probeFrame, sanitizeUrl, selectApplicationFrame, type FrameProbe } from "../frames/probe";

function probe(overrides: Partial<FrameProbe> = {}): FrameProbe {
  return {
    isTopFrame: false,
    sanitizedUrl: "https://boards.example.com/embed",
    readyState: "complete",
    formCount: 0,
    visibleInputs: 0,
    visibleTextareas: 0,
    nativeSelects: 0,
    ariaHaspopupButtons: 0,
    roleComboboxes: 0,
    requiredControls: 0,
    fileInputs: 0,
    openShadowRoots: 0,
    applicationLabelsFound: [],
    candidateCount: 0,
    bestScore: 0,
    rootConfident: false,
    topCandidates: [],
    ...overrides
  };
}

const CARRIER_TOP_FRAME = probe({
  frameId: 0,
  isTopFrame: true,
  sanitizedUrl: "https://careers.example.com/jobs/123",
  formCount: 1, // the site search box
  visibleInputs: 1,
  rootConfident: false,
  rootReason: "NO_APPLICATION_FORM",
  bestScore: 1
});

const ATS_IFRAME = probe({
  frameId: 7,
  isTopFrame: false,
  sanitizedUrl: "https://job-boards.example.com/embed/apply",
  formCount: 0,
  visibleInputs: 9,
  fileInputs: 2,
  requiredControls: 6,
  rootConfident: true,
  rootKind: "document",
  bestScore: 16,
  applicationLabelsFound: ["first_name", "last_name", "email", "resume", "submit_application"]
});

describe("frame selection", () => {
  it("chooses the iframe holding the application over the top frame", () => {
    const { chosen } = selectApplicationFrame([CARRIER_TOP_FRAME, ATS_IFRAME]);
    expect(chosen?.frameId).toBe(7);
    expect(chosen?.isTopFrame).toBe(false);
  });

  it("is not influenced by probe order", () => {
    const { chosen } = selectApplicationFrame([ATS_IFRAME, CARRIER_TOP_FRAME]);
    expect(chosen?.frameId).toBe(7);
  });

  it("records why each other frame was rejected", () => {
    const { rejected } = selectApplicationFrame([CARRIER_TOP_FRAME, ATS_IFRAME]);
    expect(rejected).toHaveLength(1);
    expect(rejected[0].frameId).toBe(0);
    expect(rejected[0].reason).toBe("NO_APPLICATION_FORM");
  });

  it("prefers a resolved root over a frame with more raw controls", () => {
    // A big unrelated form (many inputs) must not beat a resolved application.
    const noisy = probe({ frameId: 3, visibleInputs: 40, bestScore: 3, rootConfident: false });
    const { chosen } = selectApplicationFrame([noisy, ATS_IFRAME]);
    expect(chosen?.frameId).toBe(7);
  });

  it("chooses the top frame when the application is NOT in an iframe", () => {
    const topWithApp = { ...ATS_IFRAME, frameId: 0, isTopFrame: true };
    const emptyChild = probe({ frameId: 4, sanitizedUrl: "https://ads.example.com/pixel" });
    const { chosen } = selectApplicationFrame([topWithApp, emptyChild]);
    expect(chosen?.frameId).toBe(0);
    expect(chosen?.isTopFrame).toBe(true);
  });

  it("chooses nothing when no frame holds an application", () => {
    const { chosen, rejected } = selectApplicationFrame([
      CARRIER_TOP_FRAME,
      probe({ frameId: 4, sanitizedUrl: "https://ads.example.com/pixel" })
    ]);
    expect(chosen).toBeNull();
    // ...and says why, rather than failing silently.
    expect(rejected.length).toBeGreaterThan(0);
    expect(rejected[0].reason).toBeTruthy();
  });

  it("returns nothing for an empty tab rather than throwing", () => {
    expect(selectApplicationFrame([]).chosen).toBeNull();
  });

  it("accepts an unresolved frame only when application labels corroborate it", () => {
    // Root scoring can legitimately fail mid-hydration; strong labels mean the
    // application is there and a rescan is worthwhile.
    const hydrating = probe({
      frameId: 9,
      rootConfident: false,
      rootReason: "NO_APPLICATION_FORM",
      applicationLabelsFound: ["first_name", "last_name", "email", "resume"]
    });
    expect(selectApplicationFrame([hydrating]).chosen?.frameId).toBe(9);
  });
});

describe("probe sanitization", () => {
  it("strips query values and fragments from frame URLs", () => {
    // A frame URL can itself carry an application id, a token or an email.
    expect(sanitizeUrl("https://x.example.com/apply?token=abc123&email=a@b.com#z")).toBe(
      "https://x.example.com/apply"
    );
  });

  it("never throws on an unparseable URL", () => {
    expect(sanitizeUrl("about:blank#x")).toBeTruthy();
    expect(sanitizeUrl("")).toBe("(unparseable)");
  });

  it("carries counts and scores only — never page values", () => {
    document.documentElement.innerHTML = `<head></head><body>
      <label for="fn">First Name</label><input id="fn" value="SECRET-NAME" required />
      <label for="em">Email</label><input id="em" type="email" value="secret@example.com" required />
      <label for="cv">Resume/CV</label><input id="cv" type="file" />
      <button>Submit application</button>
    </body>`;

    const serialized = JSON.stringify(probeFrame(document));
    expect(serialized).not.toContain("SECRET-NAME");
    expect(serialized).not.toContain("secret@example.com");
    // ...while still carrying the evidence needed to rank the frame.
    expect(serialized).toContain("first_name");
    expect(probeFrame(document).fileInputs).toBe(1);
  });

  it("counts hidden file inputs, which Greenhouse hides behind Attach buttons", () => {
    document.documentElement.innerHTML = `<head></head><body>
      <button type="button">Attach resume</button>
      <input type="file" id="resume" style="display:none" />
    </body>`;
    expect(probeFrame(document).fileInputs).toBe(1);
  });

  it("reports the application labels it found", () => {
    document.documentElement.innerHTML = `<head></head><body>
      <label>First Name</label><input />
      <label>Work authorization</label><select><option>Yes</option></select>
    </body>`;
    const found = probeFrame(document).applicationLabelsFound;
    expect(found).toContain("first_name");
    expect(found).toContain("work_authorization");
  });
});
