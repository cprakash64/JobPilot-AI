"use client";

/**
 * A recoverable, section-scoped error state.
 *
 * Expected failures — the API is down, the session expired, a payload was
 * rejected — must not reach the global error boundary. That boundary replaces
 * the entire application with "This page couldn't load", which loses the user's
 * navigation, gives them nothing to act on, and hides which part actually
 * failed. It stays as a last resort for genuinely unexpected render crashes.
 *
 * Shows: a concise explanation, Retry, a way back to a known-good step, and the
 * request id when the server supplied one. Never a stack trace, a raw payload,
 * or anything else that could carry personal data.
 */

import { AlertTriangle, ArrowLeft, RotateCw } from "lucide-react";
import { Button } from "@/components/Button";
import { ApiError } from "@/lib/api";

export type SectionErrorInfo = {
  message: string;
  requestId?: string;
  retryable: boolean;
};

/** Convert any thrown value into a safe, user-facing section error. */
export function toSectionError(error: unknown): SectionErrorInfo {
  if (error instanceof ApiError) {
    // 401 is actionable in a specific way, so say so rather than showing the
    // backend's generic wording.
    if (error.status === 401) {
      return {
        message: "Your session has expired. Sign in again to continue editing your profile.",
        requestId: error.requestId,
        retryable: false
      };
    }
    return {
      message: error.message || "This section could not be loaded.",
      requestId: error.requestId,
      retryable: error.retryable !== false
    };
  }
  // Deliberately does not surface `error.message` for non-API errors: it may
  // contain internal detail, and it is never useful to the user.
  return {
    message: "Something went wrong loading this section.",
    retryable: true
  };
}

export function SectionError({
  error,
  onRetry,
  onBack,
  backLabel = "Back to Import",
  title = "This section could not be loaded"
}: {
  error: SectionErrorInfo;
  onRetry?: () => void;
  onBack?: () => void;
  backLabel?: string;
  title?: string;
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-[var(--danger)] bg-[var(--surface-muted)] p-5"
      data-testid="section-error"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-[var(--danger)]" />
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold text-[var(--text-primary)]">{title}</h3>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">{error.message}</p>

          <div className="mt-4 flex flex-wrap gap-3">
            {onRetry && error.retryable && (
              <Button type="button" onClick={onRetry}>
                <RotateCw aria-hidden="true" className="h-4 w-4" />
                Retry
              </Button>
            )}
            {onBack && (
              <Button type="button" variant="secondary" onClick={onBack}>
                <ArrowLeft aria-hidden="true" className="h-4 w-4" />
                {backLabel}
              </Button>
            )}
          </div>

          {error.requestId && (
            <p className="mt-3 text-xs text-[var(--text-muted)]">
              Reference: <code className="font-mono">{error.requestId}</code>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
