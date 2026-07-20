/**
 * Run-level coordination of "is there an application anywhere in this tab?".
 *
 * Why the previous guard was not enough
 * ------------------------------------
 * The earlier fix let a credible CHILD frame veto a top-frame failure. That only
 * works if the child frame already exists. On the live Airbnb page it does not:
 *
 *   t0  autofill starts, "Role overview" is selected
 *   t0  top frame resolves no root  -> publishes NO_APPLICATION_FORM (terminal)
 *   t1  user/extension reveals the Application tab
 *   t2  Airbnb inserts iframe#grnhse_iframe
 *   t3  the Greenhouse content script finally registers — too late, the run is
 *       already terminally failed and nothing resumes it
 *
 * So the veto had nothing to veto at t0. The fix is to make "no application" a
 * RUN-LEVEL verdict that a single frame cannot publish: frames report evidence,
 * and only the coordinator decides — after activation has been attempted, after
 * the frame wait has expired, and after every registered frame has answered for
 * the current run.
 *
 * This module is pure and time-injectable so the race is testable without
 * real waiting.
 */

export type RunPhase =
  | "locating_application_surface"
  | "activating_application_surface"
  | "waiting_for_application_frame"
  | "probing_application_frames"
  | "discovering_fields"
  | "filling"
  | "completed"
  | "failed";

export interface FrameReport {
  frameId: number;
  /** The run this report belongs to. Reports for older runs are ignored. */
  runId: string;
  credible: boolean;
  reason?: string;
  at: number;
}

export interface RunState {
  runId: string;
  tabId: number;
  phase: RunPhase;
  startedAt: number;
  /** Whether a safe activation control was clicked (or none existed). */
  activationAttempted: boolean;
  activationPossible: boolean;
  /** frameId -> report for THIS run. */
  reports: Map<number, FrameReport>;
  /** Frames known to exist and expected to report. */
  expectedFrames: Set<number>;
  /** Set once a terminal verdict has been published, so it happens once. */
  published: boolean;
}

/** How long to wait for a lazily-inserted application frame before giving up. */
export const APPLICATION_FRAME_TIMEOUT_MS = 15_000;
/** Quiet window after the last frame registration, for late siblings. */
export const STABILIZATION_MS = 1_500;

export function beginRun(runId: string, tabId: number, now: number): RunState {
  return {
    runId,
    tabId,
    phase: "locating_application_surface",
    startedAt: now,
    activationAttempted: false,
    activationPossible: false,
    reports: new Map(),
    expectedFrames: new Set(),
    published: false
  };
}

/** Register a frame that exists and is expected to report for this run. */
export function expectFrame(run: RunState, frameId: number): void {
  run.expectedFrames.add(frameId);
}

/**
 * Record one frame's verdict.
 *
 * Reports carrying a stale runId are dropped: a response from a previous run
 * must never influence the current one (the user may have retried, or the tab
 * may have navigated).
 */
export function recordReport(run: RunState, report: FrameReport): boolean {
  if (report.runId !== run.runId) return false;
  run.expectedFrames.add(report.frameId);
  run.reports.set(report.frameId, report);
  return true;
}

/** Any frame in this run that reported a credible application. */
export function credibleFrames(run: RunState): number[] {
  return [...run.reports.values()].filter((r) => r.credible).map((r) => r.frameId);
}

export type Verdict =
  | { kind: "wait"; because: string }
  | { kind: "proceed"; frameId: number }
  | { kind: "fail"; reason: string; summary: string };

/**
 * The single decision point. Nothing else may publish a terminal failure.
 *
 * A NO_APPLICATION_FORM verdict is terminal only when ALL of these hold:
 *   - activation was attempted, or no safe activation control exists;
 *   - the application/frame wait has expired;
 *   - every frame known to exist has reported for this run;
 *   - no frame reported a credible application root.
 */
export function evaluate(run: RunState, now: number): Verdict {
  const credible = credibleFrames(run);
  if (credible.length > 0) {
    // A credible frame always wins, even if a provisional failure was shown:
    // this is what lets a late Greenhouse iframe resume a stalled run.
    return { kind: "proceed", frameId: credible[0] };
  }

  if (run.activationPossible && !run.activationAttempted) {
    return { kind: "wait", because: "activation_not_yet_attempted" };
  }

  const elapsed = now - run.startedAt;
  if (elapsed < APPLICATION_FRAME_TIMEOUT_MS) {
    return { kind: "wait", because: "waiting_for_application_frame" };
  }

  const missing = [...run.expectedFrames].filter((id) => !run.reports.has(id));
  if (missing.length > 0) {
    // A frame that exists but has not answered may still be hydrating.
    return { kind: "wait", because: `awaiting_probe_from_${missing.length}_frame(s)` };
  }

  return {
    kind: "fail",
    reason: "NO_APPLICATION_FORM",
    // Self-contained one-liner: the terminal log must never rely on a context
    // object, which Chrome's extension error page renders as "[object Object]".
    summary:
      `no application root after ${Math.round(elapsed / 1000)}s ` +
      `frames=${run.reports.size} activated=${run.activationAttempted}`
  };
}

/**
 * A frame registering AFTER a provisional failure must be able to revive the
 * run — the user opening the Application tab manually is exactly this case.
 */
export function canResume(run: RunState, now: number): boolean {
  if (credibleFrames(run).length === 0) return false;
  // Resume regardless of `published`: the whole point is to clear a provisional
  // failure once the application actually shows up.
  return now - run.startedAt < APPLICATION_FRAME_TIMEOUT_MS + STABILIZATION_MS * 4;
}
