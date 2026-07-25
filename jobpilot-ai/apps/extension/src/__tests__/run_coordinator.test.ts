/**
 * Section B — run-level coordination, replaying the live Airbnb timeline.
 *
 * The race the previous guard could not close:
 *   t0  autofill starts, "Role overview" selected, NO Greenhouse iframe exists
 *   t0  top frame resolves no root
 *   t1  the Application surface is activated
 *   t2  Airbnb inserts iframe#grnhse_iframe
 *   t3  the Greenhouse content script registers a credible application
 *
 * At t0 there was no child frame to veto the failure, so the run went terminally
 * Failed and never resumed. "No application" must therefore be a RUN verdict,
 * not something any single frame can publish.
 */

import { describe, expect, it } from "vitest";
import {
  APPLICATION_FRAME_TIMEOUT_MS,
  beginRun,
  canResume,
  credibleFrames,
  evaluate,
  expectFrame,
  recordReport,
  type RunState
} from "../frames/runCoordinator";

const T0 = 1_000_000;

function airbnbRun(): RunState {
  const run = beginRun("run-1", 42, T0);
  run.activationPossible = true; // "Switch to application form" exists
  expectFrame(run, 0); // top frame
  return run;
}

const topFrameNoRoot = (runId = "run-1", at = T0) => ({
  frameId: 0,
  runId,
  credible: false,
  reason: "NO_APPLICATION_FORM",
  at
});

const greenhouseFrame = (runId = "run-1", at = T0 + 3000) => ({
  frameId: 7,
  runId,
  credible: true,
  at
});

describe("the live Airbnb timeline", () => {
  it("does NOT fail at t0 when the top frame has no root but activation is pending", () => {
    const run = airbnbRun();
    recordReport(run, topFrameNoRoot());

    const verdict = evaluate(run, T0 + 10);
    expect(verdict.kind).toBe("wait");
    expect(verdict.kind === "wait" && verdict.because).toBe("activation_not_yet_attempted");
  });

  it("keeps waiting after activation while the iframe is still being inserted", () => {
    const run = airbnbRun();
    recordReport(run, topFrameNoRoot());
    run.activationAttempted = true;

    const verdict = evaluate(run, T0 + 500);
    expect(verdict.kind).toBe("wait");
    expect(verdict.kind === "wait" && verdict.because).toBe("waiting_for_application_frame");
  });

  it("proceeds with the Greenhouse frame once it registers", () => {
    const run = airbnbRun();
    recordReport(run, topFrameNoRoot());
    run.activationAttempted = true;
    recordReport(run, greenhouseFrame());

    const verdict = evaluate(run, T0 + 3100);
    expect(verdict.kind).toBe("proceed");
    expect(verdict.kind === "proceed" && verdict.frameId).toBe(7);
  });

  it("a credible child frame outranks a top frame that reported no root", () => {
    const run = airbnbRun();
    run.activationAttempted = true;
    recordReport(run, topFrameNoRoot());
    recordReport(run, greenhouseFrame());
    expect(credibleFrames(run)).toEqual([7]);
  });
});

describe("terminal failure preconditions", () => {
  it("fails only after activation, timeout AND all frames have reported", () => {
    const run = airbnbRun();
    run.activationAttempted = true;
    recordReport(run, topFrameNoRoot());

    const verdict = evaluate(run, T0 + APPLICATION_FRAME_TIMEOUT_MS + 1);
    expect(verdict.kind).toBe("fail");
  });

  it("does not fail while a known frame has not answered", () => {
    const run = airbnbRun();
    run.activationAttempted = true;
    recordReport(run, topFrameNoRoot());
    expectFrame(run, 7); // the iframe exists but is still hydrating

    const verdict = evaluate(run, T0 + APPLICATION_FRAME_TIMEOUT_MS + 1);
    expect(verdict.kind).toBe("wait");
    expect(verdict.kind === "wait" && verdict.because).toMatch(/awaiting_probe/);
  });

  it("fails immediately after timeout when no activation control exists", () => {
    const run = beginRun("run-2", 42, T0);
    run.activationPossible = false; // nothing safe to click
    recordReport(run, { ...topFrameNoRoot("run-2"), runId: "run-2" });

    const verdict = evaluate(run, T0 + APPLICATION_FRAME_TIMEOUT_MS + 1);
    expect(verdict.kind).toBe("fail");
  });

  it("produces a self-contained summary, not an object", () => {
    const run = airbnbRun();
    run.activationAttempted = true;
    recordReport(run, topFrameNoRoot());

    const verdict = evaluate(run, T0 + APPLICATION_FRAME_TIMEOUT_MS + 1);
    expect(verdict.kind).toBe("fail");
    if (verdict.kind !== "fail") return;
    // This string is what reaches console.warn — it must read correctly on its
    // own, because Chrome renders a context object as "[object Object]".
    expect(verdict.summary).toMatch(/no application root after \d+s/);
    expect(verdict.summary).toMatch(/frames=\d+/);
    expect(verdict.summary).toMatch(/activated=(true|false)/);
    expect(verdict.summary).not.toContain("[object Object]");
  });
});

describe("resuming after a provisional failure", () => {
  it("resumes when the user opens the Application tab later", () => {
    const run = airbnbRun();
    run.activationAttempted = true;
    recordReport(run, topFrameNoRoot());
    run.published = true; // a provisional failure was already shown

    recordReport(run, greenhouseFrame("run-1", T0 + 8000));
    expect(canResume(run, T0 + 8100)).toBe(true);
    expect(evaluate(run, T0 + 8100).kind).toBe("proceed");
  });

  it("does not resume when no frame is credible", () => {
    const run = airbnbRun();
    recordReport(run, topFrameNoRoot());
    expect(canResume(run, T0 + 8000)).toBe(false);
  });
});

describe("stale runs and stale responses", () => {
  it("ignores a report from a previous run", () => {
    const run = airbnbRun();
    const accepted = recordReport(run, greenhouseFrame("run-OLD"));
    expect(accepted).toBe(false);
    expect(credibleFrames(run)).toEqual([]);
  });

  it("a stale credible report cannot revive the current run", () => {
    const run = airbnbRun();
    run.activationAttempted = true;
    recordReport(run, topFrameNoRoot());
    recordReport(run, greenhouseFrame("run-OLD"));
    expect(evaluate(run, T0 + APPLICATION_FRAME_TIMEOUT_MS + 1).kind).toBe("fail");
  });

  it("the newest report for a frame replaces the older one", () => {
    const run = airbnbRun();
    recordReport(run, { frameId: 7, runId: "run-1", credible: false, at: T0 });
    recordReport(run, { frameId: 7, runId: "run-1", credible: true, at: T0 + 100 });
    expect(credibleFrames(run)).toEqual([7]);
  });
});
