const ATS_FAMILIES = ["greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "workday.com", "smartrecruiters.com", "rippling.com", "bamboohr.com", "applytojob.com", "jazz.co", "teamtailor.com"];

export function normalizeApplicationUrl(raw: string): URL | null {
  try {
    const url = new URL(raw);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    url.hash = "";
    url.pathname = url.pathname.replace(/\/+$/, "") || "/";
    return url;
  } catch { return null; }
}

const IGNORED_PATH_TOKENS = new Set(["jobs", "job", "careers", "career", "apply", "application", "positions", "position"]);

function pathTokens(path: string): string[] {
  return path.toLowerCase().split(/[^a-z0-9]+/).filter((x) => x.length >= 4 && !IGNORED_PATH_TOKENS.has(x));
}

/** Same job-posting path, allowing an expected redirect suffix (e.g.
 * "/apply", "/application") or exact equality — but requiring at least one
 * shared, specific (non-generic) path token. This is what stops a stale
 * handoff for one job from ever being treated as a match for a DIFFERENT
 * job — including two different postings at the same employer on the same
 * ATS family (e.g. two different Ashby jobs both under jobs.ashbyhq.com). */
function pathsCorrespond(pathA: string, pathB: string): boolean {
  if (pathA === pathB) return true;
  const tokensA = new Set(pathTokens(pathA));
  const tokensB = pathTokens(pathB);
  if (tokensA.size === 0 || tokensB.length === 0) return false;
  return tokensB.some((token) => tokensA.has(token));
}

export function urlsMatchForHandoff(expected: string, actual: string): boolean {
  const a = normalizeApplicationUrl(expected); const b = normalizeApplicationUrl(actual);
  if (!a || !b) return false;
  const family = ATS_FAMILIES.find((f) => a.hostname === f || a.hostname.endsWith(`.${f}`));
  if (family) {
    // Same ATS family is necessary but never sufficient on its own — every
    // employer using that ATS shares the hostname, so the job-identifying
    // path must also correspond. Without this, a stale handoff for Temporal
    // could attach to an unrelated Ashby job just because both are hosted on
    // jobs.ashbyhq.com.
    if (!(b.hostname === family || b.hostname.endsWith(`.${family}`))) return false;
    return pathsCorrespond(a.pathname, b.pathname);
  }
  if (a.hostname !== b.hostname) return false;
  return pathsCorrespond(a.pathname, b.pathname);
}

export function isKnownAtsHost(raw: string): boolean {
  const url = normalizeApplicationUrl(raw);
  return Boolean(url && ATS_FAMILIES.some((f) => url.hostname === f || url.hostname.endsWith(`.${f}`)));
}
