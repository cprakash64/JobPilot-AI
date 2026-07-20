/**
 * Build identity, stamped in by build.mjs via esbuild `define`.
 *
 * A stale unpacked extension is the most common reason a live page disagrees
 * with a green test suite: the browser is running code from an earlier build.
 * Surfacing this in "Copy diagnostics" (and the collapsed widget footer) makes
 * that immediately checkable instead of a guess.
 *
 * The fallbacks apply under Vitest, where `define` is not in play.
 */
// esbuild replaces these identifiers at build time. They are declared (not
// imported from node types) because this module ships to the browser, where
// `process` does not exist.
declare const __JOBPILOT_BUILD_VERSION__: string | undefined;
declare const __JOBPILOT_BUILT_AT__: string | undefined;
declare const __JOBPILOT_BUILD_ID__: string | undefined;

function stamped(value: string | undefined): string {
  // Under Vitest the identifiers are not defined at all, so reading them
  // directly would throw a ReferenceError.
  return typeof value === "string" ? value : "dev";
}

export const BUILD_INFO = {
  version: stamped(typeof __JOBPILOT_BUILD_VERSION__ !== "undefined" ? __JOBPILOT_BUILD_VERSION__ : undefined),
  builtAt: stamped(typeof __JOBPILOT_BUILT_AT__ !== "undefined" ? __JOBPILOT_BUILT_AT__ : undefined),
  /** Short git revision; suffixed "-dirty" when the tree had uncommitted work. */
  buildId: stamped(typeof __JOBPILOT_BUILD_ID__ !== "undefined" ? __JOBPILOT_BUILD_ID__ : undefined)
} as const;

/** One-line identity for the widget footer. */
export function buildLabel(): string {
  return `v${BUILD_INFO.version} · ${BUILD_INFO.buildId}`;
}
