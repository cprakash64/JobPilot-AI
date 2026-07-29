/**
 * Channel handoff for outreach drafts.
 *
 * JobPilot never sends a message. It produces an editable draft and hands the
 * user off to LinkedIn or their email client, which is where they review and
 * send it themselves.
 *
 * Two rules drive everything here: a LinkedIn URL or an email address is used
 * only when the backend supplied a verified one, and a mailto URL is built with
 * real percent-encoding rather than string concatenation. A guessed profile URL
 * points at a real stranger, and an unencoded body silently truncates at the
 * first ampersand.
 */

/** Hosts we will open a profile on. LinkedIn regional hosts are subdomains. */
const LINKEDIN_HOSTS = ["linkedin.com"];

/**
 * Returns the URL only when it is a safe, real LinkedIn profile link.
 *
 * Rejects any non-HTTPS scheme (including `javascript:` and `data:`), any host
 * outside LinkedIn, embedded credentials, and anything that is not a `/in/`
 * profile path.
 */
export function safeLinkedInUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null;
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  const allowed = LINKEDIN_HOSTS.some(
    (candidate) => host === candidate || host.endsWith(`.${candidate}`)
  );
  if (!allowed) return null;
  if (!parsed.pathname.toLowerCase().startsWith("/in/")) return null;
  return parsed.toString();
}

/** A minimally structural email check — the backend owns real verification. */
export function safeEmailAddress(value: string | null | undefined): string | null {
  const address = (value ?? "").trim();
  if (!address || /\s/.test(address)) return null;
  const parts = address.split("@");
  if (parts.length !== 2) return null;
  const [local, domain] = parts;
  if (!local || !domain || !domain.includes(".") || domain.startsWith(".")) return null;
  return address;
}

/**
 * Builds a correctly encoded mailto URL.
 *
 * `URLSearchParams` encodes spaces as `+`, which mail clients render literally
 * in a body, so the query is assembled with `encodeURIComponent` instead. That
 * keeps newlines, ampersands, and question marks in the draft intact.
 */
export function buildMailtoUrl({
  address,
  subject,
  body
}: {
  address: string;
  subject?: string | null;
  body?: string | null;
}): string {
  const query: string[] = [];
  if (subject) query.push(`subject=${encodeURIComponent(subject)}`);
  if (body) query.push(`body=${encodeURIComponent(body)}`);
  const target = encodeURIComponent(address).replace(/%40/g, "@");
  return query.length ? `mailto:${target}?${query.join("&")}` : `mailto:${target}`;
}

/** Opens a URL in a new tab without handing the opener to the target page. */
export function openExternal(url: string): void {
  window.open(url, "_blank", "noopener,noreferrer");
}

/** Navigates to a mailto URL, which the OS hands to the default mail client. */
export function openMailClient(url: string): void {
  window.location.href = url;
}

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}
