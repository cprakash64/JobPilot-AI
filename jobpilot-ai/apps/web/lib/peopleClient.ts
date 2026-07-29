"use client";

import { api, type PeopleResponse } from "@/lib/api";

type JobId = string | number;
type Listener = (data: PeopleResponse) => void;

type CacheEntry = {
  data?: PeopleResponse;
  cachedAt?: number;
  load?: Promise<PeopleResponse>;
  discovery?: Promise<PeopleResponse>;
  broaden?: Promise<PeopleResponse>;
  listeners: Set<Listener>;
};

const CACHE_TTL_MS = 5 * 60 * 1000;
const entries = new Map<string, CacheEntry>();

function cacheKey(jobId: JobId): string {
  const token =
    typeof window === "undefined" ? "server" : window.localStorage.getItem("jobpilot_token") ?? "anonymous";
  return `${token}:${jobId}`;
}

function entryFor(jobId: JobId): CacheEntry {
  const key = cacheKey(jobId);
  const existing = entries.get(key);
  if (existing) return existing;
  const created: CacheEntry = { listeners: new Set() };
  entries.set(key, created);
  return created;
}

function publish(entry: CacheEntry, data: PeopleResponse): PeopleResponse {
  entry.data = data;
  entry.cachedAt = Date.now();
  entry.listeners.forEach((listener) => listener(data));
  return data;
}

export function getCachedPeople(jobId: JobId): PeopleResponse | null {
  const entry = entryFor(jobId);
  if (!entry.data || !entry.cachedAt || Date.now() - entry.cachedAt > CACHE_TTL_MS) return null;
  return entry.data;
}

export function subscribeToPeople(jobId: JobId, listener: Listener): () => void {
  const entry = entryFor(jobId);
  entry.listeners.add(listener);
  return () => entry.listeners.delete(listener);
}

/**
 * Every request below is keyed by job id and de-duplicated while in flight, so
 * a double-click or a rerender can never produce a second provider-backed call.
 * A caller may pass an AbortSignal to drop its own interest in the result (card
 * unmounted, query changed) without cancelling the shared request for others.
 */
function withSignal<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    promise
      .then(resolve, reject)
      .finally(() => signal.removeEventListener("abort", abort));
  });
}

export async function loadPeople(
  jobId: JobId,
  force = false,
  signal?: AbortSignal
): Promise<PeopleResponse> {
  const entry = entryFor(jobId);
  const cached = getCachedPeople(jobId);
  if (!force && cached) return cached;
  if (!entry.load) {
    entry.load = api<PeopleResponse>(`/jobs/${jobId}/people`)
      .then((data) => publish(entry, data))
      .finally(() => {
        entry.load = undefined;
      });
  }
  return withSignal(entry.load, signal);
}

export async function discoverPeople(
  jobId: JobId,
  signal?: AbortSignal
): Promise<PeopleResponse> {
  const entry = entryFor(jobId);
  if (!entry.discovery) {
    entry.discovery = api<PeopleResponse>(`/jobs/${jobId}/people/discover`, { method: "POST" })
      .then((data) => publish(entry, data))
      .finally(() => {
        entry.discovery = undefined;
      });
  }
  return withSignal(entry.discovery, signal);
}

export async function broadenPeople(
  jobId: JobId,
  signal?: AbortSignal
): Promise<PeopleResponse> {
  const entry = entryFor(jobId);
  if (!entry.broaden) {
    entry.broaden = api<PeopleResponse>(`/jobs/${jobId}/people/broaden`, {
      method: "POST"
    })
      .then((data) => publish(entry, data))
      .finally(() => {
        entry.broaden = undefined;
      });
  }
  return withSignal(entry.broaden, signal);
}

export function clearPeopleCache(): void {
  entries.clear();
}
