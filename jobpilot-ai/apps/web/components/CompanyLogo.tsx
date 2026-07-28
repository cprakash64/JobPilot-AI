"use client";

import { useMemo, useState } from "react";
import { API_URL } from "@/lib/api";

/**
 * Company logo with a real fallback chain: try the primary (proxied,
 * cached, SSRF-safe) source first, then a secondary direct source if the
 * first fails, and fall back to a deterministic generated company mark once
 * every real source has failed. That guarantees every card has a recognizable
 * visual identity without pretending an unverified domain belongs to a
 * company. The logo area is fixed so the layout never shifts.
 */
export function CompanyLogo({
  company,
  logoUrl,
  proxyPath,
  companyDomain: _companyDomain,
  size = 44
}: {
  company: string;
  logoUrl?: string | null;
  /** Relative API path for the preferred (proxied) source, e.g.
   * "/jobs/companies/acme/logo". */
  proxyPath?: string | null;
  companyDomain?: string | null;
  size?: number;
}) {
  const sources = useMemo(() => {
    const list: string[] = [];
    if (proxyPath) list.push(`${API_URL}${proxyPath}`);
    if (isTrustedLogoUrl(logoUrl)) list.push(logoUrl);
    return list;
  }, [proxyPath, logoUrl]);

  const [attempt, setAttempt] = useState(0);
  const src = sources[attempt];

  return (
    <div
      className="flex shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-white"
      // The app supports a dark theme and some company favicons (RTX is one)
      // are black-on-transparent. Pin the logo canvas to real white so a valid
      // image never looks like an empty dark square.
      style={{ width: size, height: size, backgroundColor: "#ffffff" }}
    >
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- external/proxied logo host; next/image would require per-domain config + CSP allowances
        <img
          key={src}
          src={src}
          alt={`${company} logo`}
          width={size}
          height={size}
          loading="lazy"
          className="h-full w-full object-contain p-1"
          onError={() => setAttempt((a) => a + 1)}
        />
      ) : (
        <GeneratedCompanyMark company={company} size={size} />
      )}
    </div>
  );
}

function isTrustedLogoUrl(value?: string | null): value is string {
  if (!value) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" &&
      !(
        parsed.hostname === "storage.googleapis.com" &&
        parsed.pathname.startsWith("/simplify-imgs/")
      );
  } catch {
    return false;
  }
}

const BRAND_PALETTE = [
  ["#17324D", "#D9EAF7"],
  ["#3B1D64", "#E9DDF8"],
  ["#17453B", "#D8EFE7"],
  ["#6A2E18", "#F7E2D7"],
  ["#4E3B0D", "#F6ECC8"],
  ["#3B2948", "#EADFF1"]
] as const;

function GeneratedCompanyMark({ company, size }: { company: string; size: number }) {
  const words = company
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const initials = (words.length > 1 ? `${words[0][0]}${words[1][0]}` : words[0]?.slice(0, 2) || "JP").toUpperCase();
  const hash = Array.from(company).reduce((value, character) => ((value * 31) + character.charCodeAt(0)) >>> 0, 7);
  const [foreground, background] = BRAND_PALETTE[hash % BRAND_PALETTE.length];

  return (
    <span
      data-testid="company-logo-generated"
      role="img"
      aria-label={`${company} generated company mark`}
      className="flex h-full w-full items-center justify-center font-bold tracking-[-0.04em]"
      style={{
        color: foreground,
        backgroundColor: background,
        fontSize: Math.max(11, Math.round(size * 0.33))
      }}
    >
      {initials}
    </span>
  );
}
