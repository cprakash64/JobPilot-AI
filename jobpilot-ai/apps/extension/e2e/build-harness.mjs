/** Bundle the dropdown adapters into a single IIFE the Playwright specs inject
 * into a real Chromium page. Test-only; never shipped in the extension. */
import esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["src/e2e-harness.ts"],
  bundle: true,
  format: "iife",
  target: "chrome110",
  outfile: "e2e/bundle/harness.js",
  logLevel: "error"
});
console.log("harness built -> e2e/bundle/harness.js");
