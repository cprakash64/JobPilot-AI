/**
 * Presentation helpers shared by the job list and the job detail workspace.
 * Extracted from JobDiscovery so the card and the detail panel format a role
 * identically — a salary or a posted date must never read differently on the
 * two surfaces that show the same job.
 */

export function capitalize(value: string): string {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

export function formatEmployment(value: string): string {
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map(capitalize)
    .join(" ");
}

/** Null when the employer published no salary. Callers decide whether to say
 * "Salary not listed" or to omit the field — nothing is ever invented. */
export function formatSalary(
  min?: number | null,
  max?: number | null,
  currency?: string | null
): string | null {
  if (!min && !max) {
    return null;
  }
  const symbol = currency === "USD" || !currency ? "$" : `${currency} `;
  const fmt = (value: number) => (value >= 1000 ? `${Math.round(value / 1000)}k` : `${Math.round(value)}`);
  if (min && max) {
    return `${symbol}${fmt(min)}–${fmt(max)}`;
  }
  return `${symbol}${fmt((min || max)!)}`;
}

/**
 * Whether the workplace type adds anything next to the location.
 *
 * A remote role whose location already reads "Remote" does not need to say it
 * twice; an unknown type says nothing at all.
 */
export function showsWorkplaceType(
  workplaceType: string | null | undefined,
  location: string | null | undefined
): workplaceType is string {
  if (!workplaceType || workplaceType === "unknown") {
    return false;
  }
  return !(location ?? "").toLowerCase().includes(workplaceType.toLowerCase());
}

export function daysAgo(postedAt: string | null, now: number): number | null {
  if (!postedAt) {
    return null;
  }
  const time = new Date(postedAt).getTime();
  if (Number.isNaN(time)) {
    return null;
  }
  return Math.floor((now - time) / (1000 * 60 * 60 * 24));
}

export function dateValue(postedAt: string | null): number {
  if (!postedAt) {
    return 0;
  }
  const value = new Date(postedAt).getTime();
  return Number.isNaN(value) ? 0 : value;
}

export function postedLabel(postedAt: string | null): string | null {
  const days = daysAgo(postedAt, Date.now());
  if (days === null) {
    return null;
  }
  if (days <= 0) {
    return "Posted today";
  }
  if (days === 1) {
    return "Posted 1 day ago";
  }
  return `Posted ${days} days ago`;
}

/** Compact form for the narrow list column ("2d ago"). */
export function shortPostedLabel(postedAt: string | null): string | null {
  const days = daysAgo(postedAt, Date.now());
  if (days === null) {
    return null;
  }
  if (days <= 0) {
    return "Today";
  }
  return `${days}d ago`;
}

export function isValidApplyUrl(url: string | null | undefined): boolean {
  if (!url) {
    return false;
  }
  const low = url.toLowerCase();
  if (!low.startsWith("http://") && !low.startsWith("https://")) {
    return false;
  }
  return !["example.com", "example.org", "localhost", "127.0.0.1", "test.com", "demo.com", "placeholder"].some(
    (host) => low.includes(host)
  );
}

export const SOURCE_LABELS: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  recruitee: "Recruitee",
  workable: "Workable",
  teamtailor: "Teamtailor",
  breezy: "Breezy",
  simplifyjobs: "SimplifyJobs"
};

export function sourceLabel(source: string | null): string | null {
  if (!source || source === "demo") {
    return null;
  }
  return SOURCE_LABELS[source.toLowerCase()] ?? source[0].toUpperCase() + source.slice(1);
}

/**
 * Splits a plain-text job description into paragraphs and bullet runs.
 *
 * The API already returns sanitized plain text (`description_clean`); this only
 * decides how to lay it out. No HTML is ever produced or injected.
 */
export type DescriptionBlock =
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] };

const BULLET = /^\s*(?:[-–—*•·]|\d{1,2}[.)])\s+/;

export function parseDescription(text: string): DescriptionBlock[] {
  const blocks: DescriptionBlock[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push({ kind: "list", items: list });
      list = [];
    }
  };
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      flush();
      continue;
    }
    if (BULLET.test(line)) {
      list.push(line.replace(BULLET, "").trim());
      continue;
    }
    flush();
    const previous = blocks[blocks.length - 1];
    // Re-join wrapped prose so a hard-wrapped description does not render as a
    // stack of one-line paragraphs.
    if (previous && previous.kind === "paragraph" && !/[.:;!?]$/.test(previous.text) && previous.text.length < 400) {
      previous.text = `${previous.text} ${line}`;
    } else {
      blocks.push({ kind: "paragraph", text: line });
    }
  }
  flush();
  return blocks;
}
