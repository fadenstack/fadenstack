import { afterEach, describe, expect, it } from "vitest";

import { ACCENT_KEYS, DEFAULT_ACCENT } from "./theme";
import { ACCENT_STORAGE_KEY, MODE_STORAGE_KEY, PREPAINT_SCRIPT } from "./theme-mode";

/** Runs the script as the page's <head> does, and returns what it put on <html>. */
function runPrepaint(): DOMStringMap {
  new Function(PREPAINT_SCRIPT)();
  return document.documentElement.dataset;
}

describe("the pre-paint script", () => {
  afterEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.mode;
    delete document.documentElement.dataset.accent;
  });

  it("lists the accents as the array literal JSON would write", () => {
    expect(PREPAINT_SCRIPT).toContain(`if(${JSON.stringify(ACCENT_KEYS)}.indexOf(a)<0){a="${DEFAULT_ACCENT}"}`);
    expect(PREPAINT_SCRIPT).toContain('if(["frost","graphite","aurora"].indexOf(a)<0)');
  });

  it("puts the stored mode and accent on the page", () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "light");
    window.localStorage.setItem(ACCENT_STORAGE_KEY, "aurora");
    expect(runPrepaint()).toMatchObject({ mode: "light", accent: "aurora" });
  });

  it("puts an accent it does not know back to the default", () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "dark");
    window.localStorage.setItem(ACCENT_STORAGE_KEY, "nord");
    expect(runPrepaint()).toMatchObject({ mode: "dark", accent: DEFAULT_ACCENT });
  });
});
