/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

/**
 * Component-test config, deliberately separate from `vite.config.ts`.
 *
 * The app config loads `@react-router/dev`, which wants to build the whole
 * route tree from `app/routes.ts`. A component test renders one page against
 * fixture payloads, so it needs the `~/*` path alias and nothing else.
 */
export default defineConfig({
  plugins: [tsconfigPaths()],
  test: {
    environment: "jsdom",
    setupFiles: ["./app/test/setup.ts"],
    include: ["app/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    // A page rendered in jsdom takes a second or two alone and several when
    // every file runs at once (a worker per core): page tests failed on the
    // default 5 s in full runs and passed alone.
    testTimeout: 20_000,
  },
});
