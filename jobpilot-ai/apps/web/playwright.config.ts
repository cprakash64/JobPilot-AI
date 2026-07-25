import { defineConfig, devices } from "@playwright/test";

/**
 * Real-browser suite for the web app.
 *
 * Runs against an already-running stack (`docker compose up`). The specs skip
 * themselves when the API is unreachable, so this is safe to run on a machine
 * with nothing started.
 *
 * `testDir` is scoped to ./e2e so Playwright never tries to collect the Vitest
 * suites in __tests__/.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.WEB_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure"
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }]
});
