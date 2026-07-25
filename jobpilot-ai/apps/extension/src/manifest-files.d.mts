/** Types for the shared manifest-files helper (implemented in the .mjs so it can
 * be imported by both build.mjs and the TypeScript tests). */
export interface ManifestLike {
  background?: { service_worker?: string };
  side_panel?: { default_path?: string };
  content_scripts?: { js?: string[]; css?: string[] }[];
  web_accessible_resources?: { resources?: string[] }[];
}
export function collectManifestFiles(manifest: ManifestLike): string[];
export const EXPECTED_BUILD_OUTPUTS: string[];
