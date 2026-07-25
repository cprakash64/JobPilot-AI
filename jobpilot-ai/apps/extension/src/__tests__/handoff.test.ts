/**
 * Background gating tests for the broad-host-permissions model: the ATS
 * content script is declaratively injected into every https(s) page, so the
 * ONLY thing keeping it dormant on unrelated sites is the background's
 * CONTENT_READY handshake. These tests drive that handshake directly (through
 * the real chrome.runtime.onMessage listener background.ts registers) against
 * a fake chrome + fetch, and inspect durable state via ../state so results
 * don't depend on any UI.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PendingLaunch } from "../messages";

type Listener = (raw: unknown, sender: unknown, sendResponse: (r: unknown) => void) => boolean | void;

function installFakeChrome(existingStore?: Record<string, unknown>, queryTabs: chrome.tabs.Tab[] = []) {
  const store = existingStore ?? {};
  const messageListeners: Listener[] = [];
  const installedListeners: Array<() => void> = [];
  const sentToTabs: { tabId: number; message: unknown }[] = [];
  const tabsCreated: Array<{ id: number; url: string }> = [];
  const executedScripts: Array<{ tabId: number }> = [];
  let nextTabId = 1000;

  const fakeChrome = {
    runtime: {
      onInstalled: { addListener: (fn: () => void) => installedListeners.push(fn) },
      onMessage: { addListener: (fn: Listener) => messageListeners.push(fn) },
      lastError: undefined as { message: string } | undefined,
      getManifest: () => ({ update_url: undefined, version: "0.2.0" })
    },
    storage: {
      local: {
        get: async (key: string) => ({ [key]: store[key] }),
        set: async (obj: Record<string, unknown>) => Object.assign(store, obj),
        remove: async (key: string) => { delete store[key]; }
      }
    },
    tabs: {
      onRemoved: { addListener: (_fn: unknown) => undefined },
      onUpdated: { addListener: (_fn: unknown) => undefined },
      create: vi.fn(async (opts: { url: string }) => {
        const id = nextTabId++;
        tabsCreated.push({ id, url: opts.url });
        return { id };
      }),
      get: vi.fn(async () => { throw new Error("no such tab"); }),
      update: vi.fn(async () => ({})),
      query: vi.fn(async (queryInfo: { url?: string }) => {
        if (!queryInfo?.url) return [];
        const prefix = queryInfo.url.replace(/\*$/, "");
        return queryTabs.filter((t) => (t.url ?? "").startsWith(prefix));
      }),
      sendMessage: vi.fn((tabId: number, message: unknown, cb?: (r: unknown) => void) => {
        sentToTabs.push({ tabId, message });
        cb?.(undefined);
      })
    },
    windows: { update: vi.fn(async () => ({})) },
    scripting: {
      executeScript: vi.fn(async (opts: { target: { tabId: number } }) => {
        executedScripts.push({ tabId: opts.target.tabId });
      })
    },
    permissions: {
      contains: vi.fn(async () => true),
      request: vi.fn(async () => true)
    },
    sidePanel: { setPanelBehavior: vi.fn(async () => undefined) }
  };
  (globalThis as unknown as { chrome: unknown }).chrome = fakeChrome;
  return { fakeChrome, messageListeners, installedListeners, sentToTabs, tabsCreated, executedScripts, store };
}

function installFakeFetch() {
  const impl = vi.fn(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/application-sessions/token")) {
      return new Response(JSON.stringify({ session_token: "sess-tok-abc" }), { status: 200 });
    }
    if (/\/application-sessions\/\d+\/answers$/.test(url)) {
      return new Response(JSON.stringify({ answers: [], unresolved_questions: [] }), { status: 200 });
    }
    if (/\/application-sessions\/\d+$/.test(url)) {
      return new Response(
        JSON.stringify({
          session_id: 55,
          ats_type: null,
          official_application_url: "https://careers.mongodb.com/jobs/123/apply",
          job: { title: "Software Engineer", company: "MongoDB" }
        }),
        { status: 200 }
      );
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", impl);
  return impl;
}

function dispatch(listeners: Listener[], raw: unknown, sender: unknown): Promise<unknown> {
  return new Promise((resolve) => {
    let responded = false;
    for (const fn of listeners) {
      const keepAlive = fn(raw, sender, (resp) => {
        if (!responded) { responded = true; resolve(resp); }
      });
      if (!keepAlive && !responded) {
        // Synchronous listener that declined to respond — try the next one.
        continue;
      }
    }
  });
}

function seedHandoff(overrides: Partial<PendingLaunch> = {}): PendingLaunch {
  return {
    version: 1,
    applicationId: "55",
    jobId: "1",
    applicationUrl: "https://careers.mongodb.com/jobs/123/apply",
    status: "prepared",
    handoffToken: "launch-tok",
    requestId: "req-1",
    sessionId: 55,
    launchToken: "launch-tok",
    officialUrl: "https://careers.mongodb.com/jobs/123/apply",
    expectedOrigin: "https://careers.mongodb.com",
    createdAt: Date.now(),
    expiresAt: Date.now() + 15 * 60 * 1000,
    state: "package_ready",
    protocolVersion: 3,
    atsType: null,
    ...overrides
  };
}

describe("content-script gating (arbitrary employer domains, e.g. MongoDB Careers)", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("matches a custom employer domain that has an active handoff and returns the session", async () => {
    installFakeFetch();
    const { fakeChrome, messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff());
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: "https://careers.mongodb.com/jobs/123/apply", detectedAts: null },
      { tab: { id: 1, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    )) as { ok: boolean; matched: boolean; session?: { company: string } };

    expect(resp.matched).toBe(true);
    expect(resp.ok).toBe(true);
    expect(resp.session?.company).toBe("MongoDB");
    void fakeChrome;
  });

  it("stays dormant (matched:false, HANDOFF_NOT_FOUND) on an unrelated https page with no handoff at all", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://www.google.com/", title: "Google", protocolVersion: 3, isTopFrame: true, topUrl: "https://www.google.com/", detectedAts: null },
      { tab: { id: 2, url: "https://www.google.com/" }, frameId: 0, url: "https://www.google.com/" }
    )) as { ok: boolean; matched: boolean; error?: string; session?: unknown };

    expect(resp.matched).toBe(false);
    expect(resp.error).toBe("HANDOFF_NOT_FOUND");
    expect(resp.session).toBeUndefined();
  });

  it("stays dormant on an https page whose URL does not match the active handoff", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff());
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://evil.example.com/phish", title: "Nope", protocolVersion: 3, isTopFrame: true, topUrl: "https://evil.example.com/phish", detectedAts: null },
      { tab: { id: 3, url: "https://evil.example.com/phish" }, frameId: 0, url: "https://evil.example.com/phish" }
    )) as { ok: boolean; matched: boolean; error?: string };

    expect(resp.matched).toBe(false);
    expect(resp.error).toBe("HANDOFF_URL_MISMATCH");
  });

  it("trusts a nested iframe once the tab is bound, even though the iframe's own URL looks unrelated (embedded ATS widget)", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff());
    await import("../background");

    // Top frame reports in first and binds the tab.
    await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: "https://careers.mongodb.com/jobs/123/apply", detectedAts: null },
      { tab: { id: 7, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    );

    // A nested iframe (e.g. an embedded Greenhouse widget) with an unrelated
    // URL reports in next, on the SAME tab. It must still be trusted.
    const iframeResp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://boards.greenhouse.io/embed/mongodb/1", title: "", protocolVersion: 3, isTopFrame: false, topUrl: null, detectedAts: null },
      { tab: { id: 7, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 3, url: "https://boards.greenhouse.io/embed/mongodb/1" }
    )) as { ok: boolean; matched: boolean; session?: unknown };

    expect(iframeResp.matched).toBe(true);
    expect(iframeResp.session).toBeTruthy();
  });

  it("recovers a manually-opened tab: no pre-recorded tab id, binds on the first matching CONTENT_READY", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    // Simulate "Open manually": a prepared handoff exists (staged before the
    // user clicked a plain link), but no tab was ever recorded for it.
    await state.putActive(seedHandoff());
    await import("../background");
    expect(await state.getPending(42)).toBeNull();

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: "https://careers.mongodb.com/jobs/123/apply", detectedAts: null },
      { tab: { id: 42, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    )) as { ok: boolean; matched: boolean };

    expect(resp.matched).toBe(true);
    expect((await state.getPending(42))?.targetTabId).toBe(42);
  });

  it("survives a service-worker restart: durable handoff persisted in chrome.storage.local is still resolvable after the module is reloaded", async () => {
    installFakeFetch();
    const first = installFakeChrome();
    const state1 = await import("../state");
    await state1.putActive(seedHandoff());
    await import("../background");
    await dispatch(
      first.messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: "https://careers.mongodb.com/jobs/123/apply", detectedAts: null },
      { tab: { id: 9, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    );
    expect((await state1.getPending(9))?.applicationId).toBe("55");

    // Simulate the MV3 service worker being torn down and restarted: fresh
    // module registry, fresh in-memory listener list, SAME chrome.storage.
    vi.resetModules();
    const second = installFakeChrome(first.store);
    const state2 = await import("../state");
    await import("../background");

    expect((await state2.getPending(9))?.applicationId).toBe("55");
    const resp = (await dispatch(
      second.messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: "https://careers.mongodb.com/jobs/123/apply", detectedAts: null },
      { tab: { id: 9, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    )) as { ok: boolean; matched: boolean; session?: unknown };
    expect(resp.matched).toBe(true);
    expect(resp.session).toBeTruthy();
  });

  it("never touches chrome.sidePanel to do its work — operates correctly with the side panel closed", async () => {
    installFakeFetch();
    const { fakeChrome, messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff());
    await import("../background");

    await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: "https://careers.mongodb.com/jobs/123/apply", detectedAts: null },
      { tab: { id: 11, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    );

    // setPanelBehavior is only ever wired to onInstalled (never invoked by our
    // fake onInstalled listener), and nothing else in the handoff path calls
    // chrome.sidePanel — the side panel is a purely optional viewer.
    expect(fakeChrome.sidePanel.setPanelBehavior).not.toHaveBeenCalled();
  });
});

describe("view-state failure guard (multi-frame forms)", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.unstubAllGlobals());

  it("does not regress an already-completed tab when a sibling frame reports FORM_NOT_RENDERED", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff());
    await import("../background");

    // Bind the tab and let one frame report a completed run.
    await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: "https://careers.mongodb.com/jobs/123/apply", detectedAts: null },
      { tab: { id: 21, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    );
    await dispatch(
      messageListeners,
      {
        type: "JOBPILOT_AUTOFILL_PROGRESS",
        payload: {
          state: "completed", atsId: "generic", atsDisplayName: "Generic form", limited: false,
          fieldsDiscovered: 5, filled: 5, skipped: 0, reviewRequired: 0, reachedFinalStep: true,
          documentsUploaded: [], reviewDocuments: []
        }
      },
      { tab: { id: 21 }, frameId: 0 }
    );
    expect((await state.getView(21))?.state).toBe("completed");

    // A sibling iframe with no fields of its own times out and reports
    // FORM_NOT_RENDERED — this must NOT flip the tab back to "failed".
    await dispatch(
      messageListeners,
      { type: "JOBPILOT_AUTOFILL_FAILED", reasonCode: "FORM_NOT_RENDERED", message: "No application form rendered within 30 seconds" },
      { tab: { id: 21 }, frameId: 4 }
    );

    expect((await state.getView(21))?.state).toBe("completed");
  });
});

describe("handshake + stale-handoff regressions", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.unstubAllGlobals());

  it("returns HANDOFF_NOT_FOUND (not a generic dormant no-op) when no handoff has ever been staged", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: null, detectedAts: null },
      { tab: { id: 1, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    )) as { matched: boolean; error?: string };
    expect(resp.matched).toBe(false);
    expect(resp.error).toBe("HANDOFF_NOT_FOUND");
  });

  it("returns HANDOFF_EXPIRED (not HANDOFF_NOT_FOUND) when the active handoff's TTL has passed", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    // Import (and its startup cleanup sweep) BEFORE seeding the expired
    // handoff, so this test exercises handleContentReady's own expiry check
    // rather than racing the module's fire-and-forget startup cleanup.
    await import("../background");
    await state.putActive(seedHandoff({ expiresAt: Date.now() - 1000 }));

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: null, detectedAts: null },
      { tab: { id: 2, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    )) as { matched: boolean; error?: string };
    expect(resp.matched).toBe(false);
    expect(resp.error).toBe("HANDOFF_EXPIRED");
  });

  // Regression for the exact reported bug: a PREVIOUS Temporal (Ashby)
  // handoff must never attach to a NEW, unrelated Ashby job just because
  // both are on jobs.ashbyhq.com.
  it("does not attach a stale handoff to a different job on the same ATS family host", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff({
      applicationUrl: "https://jobs.ashbyhq.com/temporal/2a3526f9",
      officialUrl: "https://jobs.ashbyhq.com/temporal/2a3526f9"
    }));
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://jobs.ashbyhq.com/acme-corp/9f81c3e2", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: null, detectedAts: null },
      { tab: { id: 3, url: "https://jobs.ashbyhq.com/acme-corp/9f81c3e2" }, frameId: 0, url: "https://jobs.ashbyhq.com/acme-corp/9f81c3e2" }
    )) as { matched: boolean; error?: string };
    expect(resp.matched).toBe(false);
    expect(resp.error).toBe("HANDOFF_URL_MISMATCH");
  });

  it("classifies a 401 session-package failure as SESSION_UNAUTHORIZED, not a generic failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff());
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: null, detectedAts: null },
      { tab: { id: 4, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    )) as { matched: boolean; error?: string };
    expect(resp.matched).toBe(true);
    expect(resp.error).toBe("SESSION_UNAUTHORIZED");

    const view = await state.getView(4);
    expect(view?.failureCode).toBe("SESSION_UNAUTHORIZED");
    expect(view?.failureRecoverable).toBe(false); // Retry would just repeat the same 401.
  });

  it("classifies a 404 session-package failure as SESSION_NOT_FOUND", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    const { messageListeners } = installFakeChrome();
    const state = await import("../state");
    await state.putActive(seedHandoff());
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/123/apply", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: null, detectedAts: null },
      { tab: { id: 5, url: "https://careers.mongodb.com/jobs/123/apply" }, frameId: 0, url: "https://careers.mongodb.com/jobs/123/apply" }
    )) as { matched: boolean; error?: string };
    expect(resp.error).toBe("SESSION_NOT_FOUND");
  });

  it("a malformed/old-schema stored handoff is treated as absent, not a crash", async () => {
    installFakeFetch();
    const { messageListeners } = installFakeChrome({
      // Simulates a handoff written by an older extension build missing
      // fields the current code assumes are always present.
      activeAssistedApplyHandoffV1: { applicationId: "1", officialUrl: "https://careers.mongodb.com/jobs/1" }
    });
    await import("../background");

    const resp = (await dispatch(
      messageListeners,
      { type: "JOBPILOT_CONTENT_READY", url: "https://careers.mongodb.com/jobs/1", title: "Apply", protocolVersion: 3, isTopFrame: true, topUrl: null, detectedAts: null },
      { tab: { id: 6, url: "https://careers.mongodb.com/jobs/1" }, frameId: 0, url: "https://careers.mongodb.com/jobs/1" }
    )) as { matched: boolean; error?: string };
    expect(resp.matched).toBe(false);
    expect(resp.error).toBe("HANDOFF_NOT_FOUND");
  });

  it("on install/update, re-injects the bridge into already-open JobPilot tabs but never an ATS/employer tab", async () => {
    installFakeFetch();
    const openTabs = [
      { id: 501, url: "https://app.jobpilot.ai/jobs" } as chrome.tabs.Tab,
      { id: 502, url: "https://careers.mongodb.com/jobs/123/apply" } as chrome.tabs.Tab
    ];
    const { installedListeners, executedScripts } = installFakeChrome(undefined, openTabs);
    await import("../background");

    expect(installedListeners.length).toBeGreaterThan(0);
    for (const fn of installedListeners) fn();
    // Flush the async revival work queued by the onInstalled listener.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    expect(executedScripts.some((s) => s.tabId === 501)).toBe(true);
    expect(executedScripts.some((s) => s.tabId === 502)).toBe(false);
  });

  it("revives an already-open JobPilot tab when the extension runtime resets even without onInstalled", async () => {
    installFakeFetch();
    const openTabs = [
      { id: 601, url: "http://localhost:3000/jobs" } as chrome.tabs.Tab
    ];
    const { executedScripts } = installFakeChrome(undefined, openTabs);

    // Importing the service worker models Developer Mode -> Reload. We do not
    // invoke the registered onInstalled callbacks in this regression test.
    await import("../background");
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    expect(executedScripts.some((s) => s.tabId === 601)).toBe(true);
  });
});
