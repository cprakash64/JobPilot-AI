// Bundles the MV3 extension into dist/ with esbuild, then copies static assets.
// Content script + sidepanel are IIFE (self-contained); background is ESM
// (MV3 service workers support type: module).
import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const outdir = "dist";
rmSync(outdir, { recursive: true, force: true });
mkdirSync(outdir, { recursive: true });

const common = { bundle: true, sourcemap: true, target: "chrome116", logLevel: "info" };

await build({
  ...common,
  format: "esm",
  entryPoints: { background: "src/background.ts" },
  outdir
});

await build({
  ...common,
  format: "iife",
  entryPoints: {
    content: "src/content/bootstrap.ts",
    sidepanel: "src/ui/sidepanel.ts"
  },
  outdir
});

cpSync("manifest.json", `${outdir}/manifest.json`);
cpSync("src/ui/sidepanel.html", `${outdir}/sidepanel.html`);
console.log("Extension built to dist/. Load unpacked from apps/extension/dist.");
