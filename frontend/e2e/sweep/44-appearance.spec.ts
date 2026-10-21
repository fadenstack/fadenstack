/**
 * The brand and the accent: the Fadenstack mark and name, and an accent
 * picked in Settings that the whole console takes, keeps over a reload, and
 * never gives to the mark.
 *
 * The accent it finds is put back at the end.
 */
import { expect, test, type Page } from "@playwright/test";

import { Watch } from "./watch";

// The current page's fill (palette.accent.navActiveBg) in dark mode.
const NAV_FILL = { frost: "rgb(76, 106, 146)", graphite: "rgb(55, 65, 81)", aurora: "rgb(63, 29, 203)" } as const;
const NAMES = { frost: "Frost", graphite: "Graphite", aurora: "Aurora" } as const;
const THREAD_ON_DARK = "#FF4D6D";

function watch(page: Page, baseURL: string | undefined) {
  return new Watch(page, new URL(baseURL!).origin);
}

async function navFill(page: Page): Promise<string> {
  const current = page.locator("a.active", { hasText: "Settings" }).first();
  await expect(current).toBeVisible();
  return current.evaluate((el) => getComputedStyle(el).backgroundColor);
}

async function threadStroke(page: Page): Promise<string | null> {
  return page.locator("svg[data-brand-mark] path").nth(1).getAttribute("stroke");
}

test.describe("the brand and the accent", () => {
  test.describe.configure({ mode: "serial" });

  test("the console carries the Fadenstack name and mark", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/dashboard");
    await expect(page).toHaveTitle(/Fadenstack/);
    await expect(page.getByText("fadenstack", { exact: true }).first()).toBeVisible();
    await expect(page.locator("svg[data-brand-mark]").first()).toBeVisible();
    await expect(page.locator('link[rel="icon"][href="/favicon.svg"]')).toHaveCount(1);
    // The old name in any case; the lowercase name only as the wordmark ("Fadenstack 0.5.0" is the product).
    await expect(page.locator("body")).not.toContainText(/llm\.port/i);
    await expect(page.locator("body")).not.toContainText(/fadenstack\b(?!-)/);
    await w.inspect();
    await w.report("brand: dashboard");
    expect(w.failures()).toEqual([]);
  });

  test("an accent picked in Settings is taken at once and kept over a reload", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/settings?tab=appearance");
    await page.evaluate(() => localStorage.setItem("faden-theme-mode", "dark"));
    await page.reload();
    const before = (await page.evaluate(() => localStorage.getItem("faden-accent"))) ?? "frost";

    try {
      for (const accent of ["aurora", "graphite", "frost"] as const) {
        const name = NAMES[accent];
        await page.getByRole("radio", { name }).check();
        await expect.poll(() => navFill(page)).toBe(NAV_FILL[accent]);
        expect(await threadStroke(page)).toBe(THREAD_ON_DARK);

        await page.reload();
        await expect(page.getByRole("radio", { name })).toBeChecked();
        expect(await page.evaluate(() => localStorage.getItem("faden-accent"))).toBe(accent);
        expect(await page.evaluate(() => document.documentElement.dataset.accent)).toBe(accent);
        await expect.poll(() => navFill(page)).toBe(NAV_FILL[accent]);
        expect(await threadStroke(page)).toBe(THREAD_ON_DARK);
      }
    } finally {
      await page.evaluate((a) => localStorage.setItem("faden-accent", a), before);
    }
    await w.inspect();
    await w.report("brand: accent");
    expect(w.failures()).toEqual([]);
  });
});
