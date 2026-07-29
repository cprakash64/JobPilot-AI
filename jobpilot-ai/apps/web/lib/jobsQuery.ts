"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";

/**
 * The Jobs workspace keeps its whole navigable state in the URL: which job is
 * open, and how the list is filtered. That is what makes reload, Back, Forward,
 * and a copied link all restore the same screen — a selected job held only in
 * component state cannot do any of that.
 *
 * Writes go through the native History API rather than the Next router so the
 * page never unmounts and never performs a route transition: opening a job is a
 * state change inside the same workspace, not a navigation to another screen.
 * Next's App Router patches pushState/replaceState, so its own router state
 * stays in sync with these writes.
 */

export type JobSort = "newest" | "fit";

export type JobsQuery = {
  /** The open job, or null for the plain list state. */
  job: number | null;
  /** Free-text role/skill filter. */
  q: string;
  /** "" | "remote" | "hybrid" | "onsite" */
  workplace: string;
  minFit: number;
  postedWithin: number;
  sort: JobSort;
};

export const JOBS_QUERY_DEFAULTS: JobsQuery = {
  job: null,
  q: "",
  workplace: "",
  minFit: 0,
  postedWithin: 7,
  sort: "newest"
};

const PARAM = {
  job: "job",
  q: "q",
  workplace: "workplace",
  minFit: "fit",
  postedWithin: "posted",
  sort: "sort"
} as const;

const QUERY_EVENT = "jobpilot:jobsquery";

const POSTED_WINDOWS = [1, 3, 7, 14, 30];
const MIN_FIT_STEPS = [0, 60, 70, 80];

function parsePositiveInt(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/** Every value is validated: a hand-edited URL can never put the list into a
 * state the controls cannot represent. */
export function parseJobsQuery(search: string): JobsQuery {
  const params = new URLSearchParams(search);
  const workplace = (params.get(PARAM.workplace) ?? "").toLowerCase();
  const minFit = Number(params.get(PARAM.minFit));
  const posted = Number(params.get(PARAM.postedWithin));
  const sort = params.get(PARAM.sort);
  return {
    job: parsePositiveInt(params.get(PARAM.job)),
    q: params.get(PARAM.q) ?? "",
    workplace: ["remote", "hybrid", "onsite"].includes(workplace) ? workplace : "",
    minFit: MIN_FIT_STEPS.includes(minFit) ? minFit : 0,
    postedWithin: POSTED_WINDOWS.includes(posted) ? posted : JOBS_QUERY_DEFAULTS.postedWithin,
    sort: sort === "fit" ? "fit" : "newest"
  };
}

function serializeJobsQuery(next: JobsQuery, currentSearch: string): string {
  // Unknown parameters (campaign tags, an experiment flag) are preserved so
  // this writer never silently drops something another part of the app set.
  const params = new URLSearchParams(currentSearch);
  const set = (key: string, value: string | null) => {
    if (value === null || value === "") {
      params.delete(key);
    } else {
      params.set(key, value);
    }
  };
  set(PARAM.job, next.job === null ? null : String(next.job));
  set(PARAM.q, next.q.trim());
  set(PARAM.workplace, next.workplace);
  set(PARAM.minFit, next.minFit > 0 ? String(next.minFit) : null);
  set(
    PARAM.postedWithin,
    next.postedWithin === JOBS_QUERY_DEFAULTS.postedWithin ? null : String(next.postedWithin)
  );
  set(PARAM.sort, next.sort === "newest" ? null : next.sort);
  return params.toString();
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(QUERY_EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(QUERY_EVENT, onChange);
  };
}

// A string snapshot is safe to return directly: it compares by value, so React
// re-renders exactly when the query really changed.
const getSnapshot = () => window.location.search;
const getServerSnapshot = () => "";

/**
 * Applies a patch to the current URL.
 *
 * `mode` decides whether the change is a navigable step. Selecting or closing a
 * job pushes (so Back returns to where the user was); typing in a filter
 * replaces (so Back is not buried under one entry per keystroke).
 */
export function updateJobsQuery(patch: Partial<JobsQuery>, mode: "push" | "replace"): void {
  if (typeof window === "undefined") return;
  const current = window.location.search;
  const next = { ...parseJobsQuery(current), ...patch };
  const search = serializeJobsQuery(next, current);
  const url = `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`;
  if (url === `${window.location.pathname}${current}${window.location.hash}`) {
    return;
  }
  if (mode === "push") {
    window.history.pushState(null, "", url);
  } else {
    window.history.replaceState(null, "", url);
  }
  window.dispatchEvent(new Event(QUERY_EVENT));
}

export function useJobsQuery(): {
  query: JobsQuery;
  setQuery: (patch: Partial<JobsQuery>, mode?: "push" | "replace") => void;
  selectJob: (jobId: number) => void;
  closeJob: () => void;
} {
  const search = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const query = useMemo(() => parseJobsQuery(search), [search]);

  const setQuery = useCallback(
    (patch: Partial<JobsQuery>, mode: "push" | "replace" = "replace") => updateJobsQuery(patch, mode),
    []
  );
  const selectJob = useCallback((jobId: number) => updateJobsQuery({ job: jobId }, "push"), []);
  const closeJob = useCallback(() => updateJobsQuery({ job: null }, "push"), []);

  return { query, setQuery, selectJob, closeJob };
}
