/**
 * Builds the injectable adapter bundle before the real-browser specs run.
 *
 * e2e/bundle/ is gitignored, so without this a clean checkout fails on a
 * missing harness.js rather than on anything to do with the code under test.
 */
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

export default function globalSetup(): void {
  const here = path.dirname(fileURLToPath(import.meta.url));
  execFileSync(process.execPath, [path.join(here, "build-harness.mjs")], {
    cwd: path.resolve(here, ".."),
    stdio: "inherit"
  });
}
