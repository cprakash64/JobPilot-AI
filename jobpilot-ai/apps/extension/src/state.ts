/**
 * Durable launch metadata is persisted in chrome.storage.local. Session
 * packages (tokens and sensitive answers) use chrome.storage.session so they
 * survive MV3 worker suspension without being written to disk.
 * workers can stop/restart, so nothing important lives only in worker memory.
 *
 * Everything is keyed by the exact employer tab id, so a launch is never
 * associated by "active tab" and the user switching tabs cannot cross wires.
 */

import type { ApplicationSessionData } from "./types";
import type { LaunchViewState, PendingLaunch } from "./messages";

const PENDING_KEY = "pendingLaunches";
const PACKAGE_KEY = "sessionPackages";
const VIEW_KEY = "viewStates";

export interface SessionPackage {
  sessionToken: string;
  session: ApplicationSessionData;
  cachedAt: number;
}

type Map<T> = Record<string, T>;

async function getMap<T>(key: string): Promise<Map<T>> {
  try {
    const store = await chrome.storage.local.get(key);
    const value = store[key];
    return value && typeof value === "object" ? (value as Map<T>) : {};
  } catch {
    return {};
  }
}

async function setMap<T>(key: string, map: Map<T>): Promise<void> {
  await chrome.storage.local.set({ [key]: map });
}

function packageStorage(): chrome.storage.StorageArea {
  // storage.session is present on supported MV3 Chrome. The fallback keeps the
  // test harness and older development browsers usable; production Chrome uses
  // session-only memory for password-bearing packages.
  return chrome.storage.session ?? chrome.storage.local;
}

async function getPackageMap(): Promise<Map<SessionPackage>> {
  try {
    const store = await packageStorage().get(PACKAGE_KEY);
    const value = store[PACKAGE_KEY];
    return value && typeof value === "object" ? value as Map<SessionPackage> : {};
  } catch {
    return {};
  }
}

async function setPackageMap(map: Map<SessionPackage>): Promise<void> {
  await packageStorage().set({ [PACKAGE_KEY]: map });
}

const ACTIVE_KEY = "activeAssistedApplyHandoffV1";

/** A handoff persisted by an older/incompatible build of the extension can be
 * missing fields the current code assumes are always present (this key
 * literally has "V1" in it from a prior schema generation). Rather than let a
 * malformed record crash downstream matching/rendering code, treat it as
 * absent — the caller sees "no handoff" (HANDOFF_NOT_FOUND) instead of a
 * thrown error, and the stale record is dropped the next time cleanup runs. */
export function isValidHandoffShape(value: unknown): value is PendingLaunch {
  if (!value || typeof value !== "object") return false;
  const v = value as Partial<PendingLaunch>;
  return (
    typeof v.applicationId === "string" &&
    typeof v.officialUrl === "string" &&
    typeof v.launchToken === "string" &&
    typeof v.sessionId === "number" &&
    typeof v.expiresAt === "number" &&
    typeof v.protocolVersion === "number"
  );
}

export async function putActive(launch: PendingLaunch): Promise<void> {
  await chrome.storage.local.set({ [ACTIVE_KEY]: launch });
}

export async function getActive(): Promise<PendingLaunch | null> {
  const store = await chrome.storage.local.get(ACTIVE_KEY);
  const value = store[ACTIVE_KEY];
  return isValidHandoffShape(value) ? value : null;
}

export async function findPendingByApplication(applicationId: string): Promise<{ tabId: number; launch: PendingLaunch } | null> {
  const map = await getMap<PendingLaunch>(PENDING_KEY);
  for (const [tabId, launch] of Object.entries(map)) {
    if (launch.applicationId === applicationId) return { tabId: Number(tabId), launch };
  }
  return null;
}

// --- PendingLaunch ---------------------------------------------------------- //
export async function putPending(tabId: number, launch: PendingLaunch): Promise<void> {
  const map = await getMap<PendingLaunch>(PENDING_KEY);
  map[String(tabId)] = { ...launch, targetTabId: tabId };
  await setMap(PENDING_KEY, map);
  await putActive(map[String(tabId)]);
}

export async function getPending(tabId: number): Promise<PendingLaunch | null> {
  const map = await getMap<PendingLaunch>(PENDING_KEY);
  const value = map[String(tabId)];
  return isValidHandoffShape(value) ? value : null;
}

/** A launch created before its tab id was known (found by requestId). */
export async function findPendingByRequest(requestId: string): Promise<{ tabId: number; launch: PendingLaunch } | null> {
  const map = await getMap<PendingLaunch>(PENDING_KEY);
  for (const [tabId, launch] of Object.entries(map)) {
    if (launch.requestId === requestId) return { tabId: Number(tabId), launch };
  }
  return null;
}

export async function updatePending(tabId: number, patch: Partial<PendingLaunch>): Promise<void> {
  const map = await getMap<PendingLaunch>(PENDING_KEY);
  const existing = map[String(tabId)];
  if (!existing) return;
  map[String(tabId)] = { ...existing, ...patch };
  await setMap(PENDING_KEY, map);
  await putActive(map[String(tabId)]);
}

// --- SessionPackage (cached to survive the single-use launch token) --------- //
export async function putPackage(tabId: number, pkg: SessionPackage): Promise<void> {
  const map = await getPackageMap();
  map[String(tabId)] = pkg;
  await setPackageMap(map);
}

export async function getPackage(tabId: number): Promise<SessionPackage | null> {
  const map = await getPackageMap();
  return map[String(tabId)] ?? null;
}

export async function findPackageBySession(sessionId: number): Promise<SessionPackage | null> {
  const map = await getPackageMap();
  return Object.values(map).find((pkg) => pkg.session.sessionId === sessionId) ?? null;
}

// --- View state (what the side panel renders) ------------------------------- //
export async function putView(tabId: number, view: LaunchViewState): Promise<void> {
  const map = await getMap<LaunchViewState>(VIEW_KEY);
  map[String(tabId)] = { ...view, tabId, updatedAt: Date.now() };
  await setMap(VIEW_KEY, map);
}

export async function getView(tabId: number): Promise<LaunchViewState | null> {
  const map = await getMap<LaunchViewState>(VIEW_KEY);
  return map[String(tabId)] ?? null;
}

export async function patchView(tabId: number, patch: Partial<LaunchViewState>): Promise<LaunchViewState | null> {
  const map = await getMap<LaunchViewState>(VIEW_KEY);
  const existing = map[String(tabId)];
  if (!existing) return null;
  const next = { ...existing, ...patch, tabId, updatedAt: Date.now() };
  map[String(tabId)] = next;
  await setMap(VIEW_KEY, map);
  return next;
}

export function initialView(tabId: number, launch: PendingLaunch, company: string | null, jobTitle: string | null): LaunchViewState {
  return {
    tabId,
    requestId: launch.requestId,
    sessionId: launch.sessionId,
    state: launch.state,
    company,
    jobTitle,
    atsId: launch.atsType,
    atsDisplayName: null,
    limited: false,
    fieldsDiscovered: 0,
    filled: 0,
    skipped: 0,
    reviewRequired: 0,
    resumeStatus: "pending",
    coverStatus: "pending",
    reachedFinalStep: false,
    contentReady: false,
    packageLoaded: false,
    running: false,
    failureCode: null,
    failureMessage: null,
    failureRecoverable: null,
    updatedAt: Date.now()
  };
}

// --- Cleanup ---------------------------------------------------------------- //
export async function clearTab(tabId: number): Promise<void> {
  for (const key of [PENDING_KEY, VIEW_KEY]) {
    const map = await getMap<unknown>(key);
    if (String(tabId) in map) {
      delete map[String(tabId)];
      await setMap(key, map);
    }
  }
  const packages = await getPackageMap();
  if (String(tabId) in packages) {
    delete packages[String(tabId)];
    await setPackageMap(packages);
  }
}

export async function cleanupExpired(now = Date.now()): Promise<void> {
  const pending = await getMap<PendingLaunch>(PENDING_KEY);
  const expiredTabs = Object.entries(pending).filter(([, launch]) => launch.expiresAt <= now).map(([tabId]) => tabId);
  for (const tabId of expiredTabs) delete pending[tabId];
  if (expiredTabs.length) {
    await setMap(PENDING_KEY, pending);
    const views = await getMap<unknown>(VIEW_KEY);
    for (const tabId of expiredTabs) delete views[tabId];
    await setMap(VIEW_KEY, views);
    const packages = await getPackageMap();
    for (const tabId of expiredTabs) delete packages[tabId];
    await setPackageMap(packages);
  }
  const active = await getActive();
  if (active && active.expiresAt <= now) await chrome.storage.local.remove(ACTIVE_KEY);
}

export const STORAGE_KEYS = { PENDING_KEY, PACKAGE_KEY, VIEW_KEY, ACTIVE_KEY };
