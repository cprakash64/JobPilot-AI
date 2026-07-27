"use client";

import { api, type PeopleResponse } from "@/lib/api";

type JobId = string | number;
type Listener = (data: PeopleResponse) => void;

type CacheEntry = {
  data?: PeopleResponse;
  cachedAt?: number;
  load?: Promise<PeopleResponse>;
  discovery?: Promise<PeopleResponse>;
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

export async function loadPeople(jobId: JobId, force = false): Promise<PeopleResponse> {
  const entry = entryFor(jobId);
  const cached = getCachedPeople(jobId);
  if (!force && cached) return cached;
  if (entry.load) return entry.load;

  entry.load = api<PeopleResponse>(`/jobs/${jobId}/people`)
    .then((data) => publish(entry, data))
    .finally(() => {
      entry.load = undefined;
    });
  return entry.load;
}

export async function discoverPeople(jobId: JobId): Promise<PeopleResponse> {
  const entry = entryFor(jobId);
  if (entry.discovery) return entry.discovery;

  entry.discovery = api<PeopleResponse>(`/jobs/${jobId}/people/discover`, { method: "POST" })
    .then((data) => publish(entry, data))
    .finally(() => {
      entry.discovery = undefined;
    });
  return entry.discovery;
}

export function clearPeopleCache(): void {
  entries.clear();
}
