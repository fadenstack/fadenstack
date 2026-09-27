import { defineConfig, devices } from "@playwright/test";

/**
 * The UI sweep: every page and the main flows, against a running install.
 *
 *   E2E_BASE_URL=http://10.250.10.61 E2E_COOKIE=<fapiauth> npx playwright test -c playwright.sweep.config.ts
 *
 * Unlike the journey suite (playwright.config.ts) it never touches the fleet:
 * no cluster, deployment or node is created, changed or removed. What its
 * flows create -- a chat, a project, a container, a skill -- they remove
 * again. Signed in with E2E_COOKIE (a console session cookie) on an installed
 * server, or with the dev-only login on a dev stack.
 *
 * Every page's problems -- page errors, console errors, failed API calls,
 * untranslated keys, error screens -- are written to
 * test-results/sweep/report.jsonl, and a screenshot of each page to
 * test-results/sweep/pages/.
 *
 * Against an install on HTTPS with a certificate made there (llmport tls
 * generate), set E2E_IGNORE_HTTPS_ERRORS=1 unless this machine trusts the
 * install's tls/ca.crt: the pages are still served and checked over HTTPS.
 */
export const SWEEP_STATE = "e2e/.auth/sweep.json";

export default defineConfig({
  testDir: "./e2e/sweep",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  outputDir: "test-results/sweep/artifacts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    colorScheme: "dark",
    locale: "en-US",
    ignoreHTTPSErrors: process.env.E2E_IGNORE_HTTPS_ERRORS === "1",
  },
  projects: [
    { name: "setup", testMatch: /sweep\.setup\.ts/ },
    {
      name: "chromium",
      dependencies: ["setup"],
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        storageState: SWEEP_STATE,
      },
    },
  ],
});
