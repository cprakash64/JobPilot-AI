"use client";

import { useState } from "react";

/**
 * Company logo with a safe fallback.
 *
 * Renders the resolved company logo image when a URL is available; if the image
 * is missing or fails to load (404, blocked, network error) it falls back to a
 * clean initial-letter avatar. A logo never blocks or breaks the job card.
 */
export function CompanyLogo({
  company,
  logoUrl,
  size = 44
}: {
  company: string;
  logoUrl?: string | null;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const showImage = Boolean(logoUrl) && !failed;

  return (
    <div
      className="flex shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-white"
      style={{ width: size, height: size }}
    >
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- external logo host; next/image would require per-domain config + CSP allowances
        <img
          src={logoUrl as string}
          alt={`${company} logo`}
          width={size}
          height={size}
          loading="lazy"
          className="h-full w-full object-contain p-1"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="text-sm font-semibold text-pine" aria-hidden>
          {initials(company)}
        </span>
      )}
    </div>
  );
}

function initials(company: string): string {
  const value = (company || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
  return value || "?";
}
