import { defineConfig, devices } from "@playwright/test";

/**
 * Real-browser dropdown suite (section M).
 *
 * The specs load a local `file://` fixture and inject the bundled adapters, so
 * there is no baseURL and no dev server. `globalSetup` rebuilds that bundle
 * first — e2e/bundle/ is gitignored, so a clean checkout would otherwise fail
 * on a missing harness rather than on the code under test.
 */
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    trace: "retain-on-failure"
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }]
});
