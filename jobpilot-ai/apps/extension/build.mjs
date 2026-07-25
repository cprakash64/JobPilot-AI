// Bundles the MV3 extension into dist/ with esbuild, then copies static assets.
// Content script + sidepanel are IIFE (self-contained); background is ESM
// (MV3 service workers support type: module).
import { build } from "esbuild";
import { cpSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { execSync } from "node:child_process";
import { readFileSync as readSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

const outdir = "dist";

// --------------------------------------------------------------------------- //
// Build identity
//
// A stale unpacked extension is the single most common reason the live page
// disagrees with a green test suite. Stamping the same identity into BOTH the
// bundle and the generated manifest means "Copy diagnostics" can prove which
// build is actually running in the browser.
// --------------------------------------------------------------------------- //
const sourceManifest = JSON.parse(readSync("manifest.json", "utf-8"));
const BUILD_VERSION = sourceManifest.version;
const BUILT_AT = new Date().toISOString();
/**
 * Build identity.
 *
 * A bare "<sha>-dirty" is NOT enough: every uncommitted build produced the same
 * id, so a stale loaded extension was indistinguishable from a fresh one — which
 * is exactly the confusion that made live debugging unreliable. When the tree is
 * dirty we append a short content hash of the actual build inputs, so any real
 * source change yields a different id, and rebuilding unchanged sources yields
 * the same one.
 */
function sourceFingerprint() {
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      // Tests do not ship, so they must not change the shipped build id.
      else if (/\.(ts|tsx|mjs|json|html|css)$/.test(entry.name) && !full.includes("__tests__")) {
        files.push(full);
      }
    }
  };
  walk("src");
  files.push("manifest.json", "build.mjs");

  const hash = createHash("sha256");
  for (const file of files.sort()) {
    hash.update(file);
    hash.update(readSync(file));
  }
  return hash.digest("hex").slice(0, 7);
}

let BUILD_ID = "nogit";
try {
  BUILD_ID = execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] })
    .toString()
    .trim();
  const dirty = execSync("git status --porcelain", { stdio: ["ignore", "pipe", "ignore"] })
    .toString()
    .trim();
  // e.g. d96055b-dirty-a1b2c3d
  if (dirty) BUILD_ID += `-dirty-${sourceFingerprint()}`;
} catch {
  // Not a git checkout (or git unavailable) — fall back to content identity.
  try {
    BUILD_ID = `nogit-${sourceFingerprint()}`;
  } catch {
    /* keep "nogit" */
  }
}

const buildDefine = {
  __JOBPILOT_BUILD_VERSION__: JSON.stringify(BUILD_VERSION),
  __JOBPILOT_BUILT_AT__: JSON.stringify(BUILT_AT),
  __JOBPILOT_BUILD_ID__: JSON.stringify(BUILD_ID)
};
rmSync(outdir, { recursive: true, force: true });
mkdirSync(outdir, { recursive: true });

const common = { bundle: true, sourcemap: true, target: "chrome116", logLevel: "info", define: buildDefine };

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

// Write the manifest with the same identity compiled into the bundle, so the
// two can never disagree about which build is loaded.
{
  const generated = { ...sourceManifest, version_name: `${BUILD_VERSION} (${BUILD_ID} ${BUILT_AT})` };
  writeFileSync(`${outdir}/manifest.json`, JSON.stringify(generated, null, 2));
}
cpSync("src/ui/sidepanel.html", `${outdir}/sidepanel.html`);

// Fail the build if the manifest references a file that does not exist in dist/
// (a renamed entry point or a typo would otherwise ship a broken extension).
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { collectManifestFiles } from "./src/manifest-files.mjs";

const manifest = JSON.parse(readFileSync(`${outdir}/manifest.json`, "utf-8"));
const missing = collectManifestFiles(manifest).filter((f) => !existsSync(`${outdir}/${f}`));
if (missing.length) {
  console.error(`Manifest references missing dist files: ${missing.join(", ")}`);
  process.exit(1);
}

console.log(`Extension built to dist/ — v${BUILD_VERSION} build ${BUILD_ID} at ${BUILT_AT}`);
console.log("Load unpacked from apps/extension/dist. After rebuilding: chrome://extensions -> Reload,");
console.log("then close and reopen the application tab so the new content script is injected.");
