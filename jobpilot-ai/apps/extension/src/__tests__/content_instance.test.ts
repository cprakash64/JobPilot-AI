import { describe, expect, it, vi } from "vitest";
import { claimContentInstance, makeContentInstanceId } from "../content/instance";

describe("content-script reload ownership", () => {
  it("lets a newly injected instance supersede an orphaned page instance", () => {
    const pageWorld: Record<string, unknown> = { __jobpilotContentLoaded: true };
    const firstIsCurrent = claimContentInstance(pageWorld, "old-runtime");
    expect(firstIsCurrent()).toBe(true);

    const nextIsCurrent = claimContentInstance(pageWorld, "new-runtime");
    expect(firstIsCurrent()).toBe(false);
    expect(nextIsCurrent()).toBe(true);
    // The legacy boolean never blocks the new owner.
    expect(pageWorld.__jobpilotContentLoaded).toBe(true);
  });

  it("creates a distinct id for repeated injections of the same build", () => {
    vi.spyOn(Math, "random").mockReturnValueOnce(0.1).mockReturnValueOnce(0.2);
    expect(makeContentInstanceId("build-a")).not.toBe(makeContentInstanceId("build-a"));
  });
});
