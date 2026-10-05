/**
 * The guides: the getting-started list, the console tour, the first steps of
 * every how-to, the page help's "Show me how", and resetting them.
 *
 * Nothing is created: a how-to is followed only as far as opening its dialog,
 * then closed (the token guide not even that, its action makes a token). The
 * signed-in user's guide progress and getting-started state are put back.
 * Language-neutral: everything is found by its data attributes.
 */
import { expect, test, type Page } from "@playwright/test";

import { Watch } from "./watch";

const PREFS = "/api/admin/users/me/preferences";

type Prefs = {
  getting_started?: unknown;
  tour_progress?: Record<string, unknown> | null;
};

function watch(page: Page, baseURL: string | undefined) {
  return new Watch(page, new URL(baseURL!).origin);
}

async function readPrefs(page: Page): Promise<Prefs> {
  const response = await page.request.get(PREFS);
  expect(response.ok()).toBeTruthy();
  return ((await response.json()) as { preferences: Prefs }).preferences ?? {};
}

async function writePrefs(page: Page, preferences: Record<string, unknown>) {
  const response = await page.request.patch(PREFS, { data: { preferences } });
  expect(response.ok(), `PATCH preferences: ${response.status()}`).toBeTruthy();
}

/** Puts the guide state back as *saved* had it; keys added since are removed. */
async function restorePrefs(page: Page, saved: Prefs) {
  const now = await readPrefs(page);
  const keys = new Set([...Object.keys(now.tour_progress ?? {}), ...Object.keys(saved.tour_progress ?? {})]);
  await writePrefs(page, {
    getting_started: saved.getting_started ?? null,
    tour_progress: Object.fromEntries([...keys].map((k) => [k, saved.tour_progress?.[k] ?? null])),
  });
}

async function openHelp(page: Page, item: string) {
  await page.locator("[data-tour-id='topbar.help']").click();
  await page.locator(`[data-help-item='${item}']`).click();
}

const tooltip = (page: Page) => page.locator("[data-guide-tooltip]");

async function stepNumber(page: Page): Promise<number> {
  const text = await tooltip(page).locator("[data-guide-counter]").innerText();
  return Number(/\d+/.exec(text)?.[0]);
}

test.describe("guides", () => {
  test.describe.configure({ mode: "serial" });

  test("the getting-started list ticks itself, hides, and stays under Help", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/dashboard");
    const saved = await readPrefs(page);
    try {
      await writePrefs(page, { getting_started: null });
      await page.reload();
      const card = page.locator("[data-tour-id='dashboard.getting-started']");
      await expect(card).toBeVisible();
      for (const id of ["machine", "cluster", "model", "chat", "people"]) {
        await expect(card.locator(`[data-checklist-item='${id}']`)).toHaveAttribute("data-done", /true|false/);
      }
      await w.inspect();

      // Hiding it is remembered.
      await card.locator("[data-checklist-hide]").click();
      await expect(card).toBeHidden();
      expect(((await readPrefs(page)).getting_started as { dismissed?: boolean })?.dismissed).toBe(true);

      // The Help menu still has it.
      await openHelp(page, "getting_started");
      const dialog = page.getByRole("dialog");
      await expect(dialog.locator("[data-checklist-item='machine']")).toBeVisible();
      await page.keyboard.press("Escape");
      await w.report("guides: getting started");
      expect(w.failures()).toEqual([]);
    } finally {
      await restorePrefs(page, saved);
    }
  });

  test("the console tour walks to the end and is recorded", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/dashboard");
    const saved = await readPrefs(page);
    try {
      await openHelp(page, "console_tour");
      await expect(tooltip(page)).toBeVisible();
      await w.inspect();
      let last = 0;
      for (let i = 0; i < 20; i++) {
        const n = await stepNumber(page);
        expect(n, "the tour goes forward").toBeGreaterThan(last);
        last = n;
        // Every step points at something on screen.
        await expect(page.locator(".guide-target")).toHaveCount(1);
        const next = tooltip(page).locator("[data-guide-next]");
        const finishing = n === Number(/(\d+)\D*$/.exec(await tooltip(page).locator("[data-guide-counter]").innerText())?.[1]);
        await next.click();
        if (finishing) break;
        await expect.poll(() => stepNumber(page)).toBeGreaterThan(n);
      }
      await expect(tooltip(page)).toBeHidden();
      await expect
        .poll(async () => ((await readPrefs(page)).tour_progress?.guide_console_tour as { completed?: boolean })?.completed)
        .toBe(true);
      await w.report("guides: console tour");
      expect(w.failures()).toEqual([]);
    } finally {
      await restorePrefs(page, saved);
    }
  });

  test("every how-to starts on its page and follows the first click", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/dashboard");
    await openHelp(page, "how_to_guides");
    const rows = page.locator("[data-guide-row]");
    await expect(rows.first()).toBeVisible();
    const guides = await rows.evaluateAll((els) => els.map((el) => (el as HTMLElement).dataset.guideRow!));
    await page.keyboard.press("Escape");
    expect(guides.length).toBeGreaterThan(0);

    for (const guide of guides) {
      await page.goto("/admin/dashboard");
      await openHelp(page, "how_to_guides");
      await page.locator(`[data-guide-row='${guide}'] button`).click();
      await expect(tooltip(page), guide).toBeVisible();
      await w.inspect();

      // Read up to the first step that asks for a click, then make it: it opens
      // the guide's dialog or drawer and the guide must follow. The token
      // guide's click would make a token, so it stops before.
      for (let i = 0; i < 6 && (await tooltip(page).locator("[data-guide-next]").count()) > 0; i++) {
        const n = await stepNumber(page);
        await tooltip(page).locator("[data-guide-next]").click();
        await expect.poll(() => stepNumber(page).catch(() => 0), { message: guide }).toBeGreaterThan(n);
      }
      const target = page.locator(".guide-target");
      if (guide !== "create_api_token" && (await target.isEnabled().catch(() => false))) {
        const n = await stepNumber(page);
        await target.click();
        await expect.poll(() => stepNumber(page).catch(() => 0), { message: `${guide} follows the click` }).toBe(n + 1);
        await w.inspect();
      }
      await tooltip(page).locator("[data-guide-close]").click();
      await expect(tooltip(page)).toBeHidden();
      await page.keyboard.press("Escape");
      await w.report(`guides: ${guide}`);
    }
    expect(w.failures()).toEqual([]);
  });

  test("page help explains each page and offers its guide", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    for (const route of ["/admin/nodes", "/admin/clusters", "/admin/marketplace", "/admin/llm/providers", "/admin/users", "/admin/profile"]) {
      await page.goto(route);
      // F1 works once the console is up.
      await expect(page.locator("[data-tour-id='topbar.help']")).toBeVisible();
      await page.keyboard.press("F1");
      const showMe = page.locator("[data-page-help-guide]");
      await expect(showMe, route).toBeVisible();
      await w.inspect();
      await showMe.click();
      await expect(tooltip(page), route).toBeVisible();
      await tooltip(page).locator("[data-guide-close]").click();
      await expect(tooltip(page)).toBeHidden();
    }
    // A page without a guide still has its help.
    await page.goto("/admin/logs");
    await openHelp(page, "about_page");
    await expect(page.locator(".MuiDrawer-paper h6").first()).toBeVisible();
    await expect(page.locator("[data-page-help-guide]")).toHaveCount(0);
    await w.inspect();
    await w.report("guides: page help");
    expect(w.failures()).toEqual([]);
  });

  test("resetting the guides brings the list back", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/dashboard");
    const saved = await readPrefs(page);
    try {
      await writePrefs(page, {
        getting_started: { dismissed: true },
        tour_progress: { guide_console_tour: { version: 2, completed: true, current_step: -1, skipped_steps: [] } },
      });
      await page.reload();
      await expect(page.locator("[data-tour-id='dashboard.getting-started']")).toBeHidden();
      await openHelp(page, "reset_guides");
      await expect(page.locator("[data-tour-id='dashboard.getting-started']")).toBeVisible();
      const after = await readPrefs(page);
      expect(after.getting_started ?? null).toBeNull();
      expect(after.tour_progress?.guide_console_tour ?? null).toBeNull();
      await w.report("guides: reset");
      expect(w.failures()).toEqual([]);
    } finally {
      await restorePrefs(page, saved);
    }
  });
});
