/**
 * Application-email validation (client mirror of app/profile/emails.py).
 *
 * RFC 2606 / RFC 6761 reserve these domains for documentation. Mail to them can
 * never be delivered, so an application carrying one LOOKS complete while being
 * unreachable — worse than leaving the field blank. The server is the authority;
 * this exists so the user gets the message as they type rather than after a
 * round trip.
 */

const RESERVED_DOMAINS = [
  "example.com", "example.net", "example.org", "example.edu",
  "invalid", "localhost", "test"
];

export const RESERVED_EMAIL_MESSAGE =
  "Enter a real email address that can receive employer messages.";

/** True for RFC-reserved example/test domains, including subdomains. */
export function isReservedEmailDomain(value: string | null | undefined): boolean {
  const address = (value ?? "").trim().toLowerCase();
  if (!address.includes("@")) return false;
  const domain = address.split("@").pop()?.replace(/^\.+|\.+$/g, "") ?? "";
  if (!domain) return false;
  return RESERVED_DOMAINS.some((r) => domain === r || domain.endsWith(`.${r}`));
}

/** Validate the Application email field. Returns null when acceptable. */
export function validateApplicationEmail(value: string | null | undefined): string | null {
  const address = (value ?? "").trim();
  // Optional: an empty field is surfaced by readiness, not as a format error.
  if (!address) return null;
  // Reserved domains are checked FIRST so they always get the specific,
  // actionable message. Some of them (localhost, invalid) have no dot and would
  // otherwise fall out as a generic format error — and differ from the server's
  // message for the same input.
  if (isReservedEmailDomain(address)) return RESERVED_EMAIL_MESSAGE;
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(address)) return "Enter a valid email address.";
  return null;
}
