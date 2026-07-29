"use client";

import { ExternalLink } from "lucide-react";
import { isValidApplyUrl } from "@/components/jobs/format";

/**
 * Primary apply control. For a valid official URL it launches the assisted
 * application flow (prepare docs + secure session, then open the employer site);
 * otherwise it shows the same "no official link" state as before.
 */
export function AssistedApplyButton({
  url,
  onApply,
  size = "md"
}: {
  url: string | null | undefined;
  onApply: () => void;
  size?: "md" | "lg";
}) {
  const height = size === "lg" ? "h-11 px-5" : "h-10 px-4";
  if (!isValidApplyUrl(url)) {
    return (
      <span className={`inline-flex ${height} items-center rounded-md bg-panel text-sm text-[var(--text-muted)]`}>
        No official link available
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onApply();
      }}
      className={`focus-ring inline-flex ${height} items-center gap-2 rounded-md bg-pine text-sm font-medium text-white transition-colors hover:bg-[var(--accent-hover)]`}
    >
      Apply on official site <ExternalLink className="h-4 w-4" />
    </button>
  );
}
