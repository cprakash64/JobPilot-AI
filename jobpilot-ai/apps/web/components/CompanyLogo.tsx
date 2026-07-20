"use client";

import { useMemo, useState } from "react";
import { API_URL } from "@/lib/api";

/**
 * Company logo with a real fallback chain: try the primary (proxied,
 * cached, SSRF-safe) source first, then a secondary direct source if the
 * first fails, and only fall back to a neutral JobPilot placeholder — never
 * an initial-letter avatar, and never a broken-image icon — once every real
 * source has failed. The logo area is a fixed square so the layout never
 * shifts as sources are tried.
 */
export function CompanyLogo({
  company,
  logoUrl,
  proxyPath,
  size = 44
}: {
  company: string;
  logoUrl?: string | null;
  /** Relative API path for the preferred (proxied) source, e.g.
   * "/jobs/companies/acme/logo". */
  proxyPath?: string | null;
  size?: number;
}) {
  const sources = useMemo(() => {
    const list: string[] = [];
    if (proxyPath) list.push(`${API_URL}${proxyPath}`);
    if (logoUrl) list.push(logoUrl);
    return list;
  }, [proxyPath, logoUrl]);

  const [attempt, setAttempt] = useState(0);
  const src = sources[attempt];

  return (
    <div
      className="flex shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-white"
      style={{ width: size, height: size }}
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
        <PlaceholderMark size={size} />
      )}
    </div>
  );
}

/** Neutral JobPilot placeholder — deliberately not the company's initials, so
 * a real-but-unresolved logo is never confused with a verified one. */
function PlaceholderMark({ size }: { size: number }) {
  return (
    <svg
      data-testid="company-logo-placeholder"
      width={Math.round(size * 0.55)}
      height={Math.round(size * 0.55)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
      className="text-[var(--text-muted)]"
    >
      <rect x="3" y="7" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M3 12h18" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}
