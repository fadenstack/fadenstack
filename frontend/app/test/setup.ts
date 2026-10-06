/** Global setup for component tests. */
import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { afterEach, vi } from "vitest";

import chat from "../../../backend/i18n/en/chat.json";
import common from "../../../backend/i18n/en/common.json";
import tour from "../../../backend/i18n/en/tour.json";

// The English strings the app ships, so a page renders what an English
// reader sees -- and a key missing from them renders as the key, which the
// assertions then fail on instead of passing over it.
void i18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  defaultNS: "common",
  ns: ["common", "chat", "tour"],
  resources: { en: { common, chat, tour } },
  interpolation: { escapeValue: false },
  react: { useSuspense: false },
});

afterEach(() => {
  cleanup();
});

// findBy* and waitFor give up after one second by default. With the whole
// suite running in parallel a render can take longer than that: the muting
// test of the attention widget failed so under load and passed alone. The
// test timeout itself (vitest.config.ts) stays the limit for a real hang.
configure({ asyncUtilTimeout: 5000 });

// jsdom implements neither of these, and MUI reads both during layout.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
