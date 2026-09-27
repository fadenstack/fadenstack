/**
 * On a phone: the chat's navigation, composer and $-picker, the console's
 * navigation, and no page wider than the screen.
 */
import { devices, expect, test, type Page } from "@playwright/test";

import { Watch } from "./watch";

const { defaultBrowserType: _browser, ...pixel } = devices["Pixel 7"];
test.use(pixel);

/** Nothing sticks out sideways: the page scrolls up and down only. */
async function fitsTheScreen(page: Page) {
  const { scroll, width } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: window.innerWidth,
  }));
  expect(scroll, `the page is ${scroll}px wide on a ${width}px screen`).toBeLessThanOrEqual(width + 1);
}

test.describe("on a phone", () => {
  test("the chat opens its navigation, answers, and fits the screen", async ({ page, baseURL }) => {
    const watch = new Watch(page, new URL(baseURL!).origin);
    await page.goto("/chat");
    await fitsTheScreen(page);
    await page.getByRole("button", { name: "Open navigation" }).first().click();
    await expect(page.getByRole("button", { name: "New chat" }).first()).toBeVisible();
    await page.getByRole("button", { name: "New chat" }).first().click();

    await page.getByPlaceholder(/Type a message/).fill("On a phone: say ok");
    await page.getByRole("button", { name: "Send" }).click();
    await page.waitForURL(/\/chat\/[0-9a-f-]{36}/);
    await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0, { timeout: 120_000 });
    await fitsTheScreen(page);

    const composer = page.getByPlaceholder(/Type a message/);
    await composer.click();
    await composer.pressSequentially("$bug", { delay: 50 });
    await expect(
      page.getByRole("option", { name: /Bug report triage/ }).or(page.getByRole("menuitem", { name: /Bug report triage/ })).first(),
    ).toBeVisible();
    await watch.inspect();
    await watch.report("phone: chat");
    await page.request.delete(`/api/chat/sessions/${page.url().split("/chat/")[1]}`);
    expect(watch.failures()).toEqual([]);
  });

  test("the console opens its navigation and its pages fit the screen", async ({ page, baseURL }) => {
    const watch = new Watch(page, new URL(baseURL!).origin);
    await page.goto("/admin/dashboard");
    await fitsTheScreen(page);
    await page.getByRole("button", { name: "Open navigation" }).first().click();
    // Not disabled: the sortable menu marked every entry aria-disabled on a phone.
    const menu = page.getByRole("dialog", { name: "Navigation" });
    await menu.getByRole("link", { name: "Containers" }).first().click();
    const page2 = menu.getByRole("link", { name: "Containers" }).nth(1);
    if (await page2.count()) await page2.click();
    await page.waitForURL(/\/admin\/containers/);
    await watch.inspect();
    await fitsTheScreen(page);
    await watch.report("phone: console");
    for (const path of ["/admin/observability", "/admin/mcp/servers", "/admin/skills", "/admin/logs"]) {
      await page.goto(path);
      await watch.inspect();
      await fitsTheScreen(page);
    }
    expect(watch.failures()).toEqual([]);
  });
});
