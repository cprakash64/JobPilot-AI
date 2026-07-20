import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url))
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // e2e/ holds the Playwright suite (`npm run test:e2e`). Vitest cannot run
    // those — it would fail on test.beforeEach() from a different test runner.
    exclude: ["node_modules/**", "dist/**", ".next/**", "e2e/**"]
  }
});
