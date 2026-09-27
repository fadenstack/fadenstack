/**
 * The console's working parts: container tiles and a container's life, a
 * skill made and removed, an MCP server tested, the theme and the language.
 *
 * What it makes -- a container, a skill -- it removes again; the theme and
 * the language are put back.
 */
import { expect, test, type Page } from "@playwright/test";

import { Watch } from "./watch";

const STAMP = Date.now().toString(36);
const CONTAINER = `sweep-${STAMP}`;

function watch(page: Page, baseURL: string | undefined) {
  return new Watch(page, new URL(baseURL!).origin);
}

test.describe("the console", () => {
  test.describe.configure({ mode: "serial" });

  test("container tiles filter the list", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/containers");
    const tiles = page.getByRole("group", { name: /Containers by state/ });
    await expect(tiles).toBeVisible();
    await expect(page.getByText("faden-postgres").first()).toBeVisible();
    await tiles.getByText("Not running").click();
    await expect(page.getByText("faden-postgres")).toHaveCount(0);
    await tiles.getByText("Not running").click();
    await expect(page.getByText("faden-postgres").first()).toBeVisible();
    await tiles.getByText("Running").first().click();
    await expect(page.getByText("faden-postgres").first()).toBeVisible();
    await w.inspect();
    await w.report("admin: container tiles");
    expect(w.failures()).toEqual([]);
  });

  test("a container is created, edited and deleted", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/containers/new");
    await page.getByLabel(/^Image/).first().fill("node:22-alpine");
    await page.keyboard.press("Escape");
    await page.getByLabel("Container Name").fill(CONTAINER);
    await page.getByLabel("Command Override").fill(
      `node -e "require('http').createServer((q, s) => s.end(process.env.GREETING)).listen(8080)"`,
    );
    const env = page.getByText("Environment Variables").locator("xpath=ancestor::div[contains(@class,'MuiPaper')][1]");
    await env.getByRole("button", { name: "Add" }).click();
    await page.getByLabel("Key").fill("GREETING");
    await page.getByLabel("Value").fill("first");
    await page.getByLabel("Start immediately after creation").check();
    await page.getByRole("button", { name: "Create & Start" }).click();
    await page.waitForURL(/\/admin\/containers\/(?!new)[0-9a-f]{64}$/, { timeout: 60_000 });
    const first = page.url();

    await page.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByText(`Edit ${CONTAINER}`)).toBeVisible();
    await page.getByLabel("Value").fill("second");
    await page.getByRole("button", { name: "Save and replace" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Replace" }).click();
    await page.waitForURL((url) => /\/admin\/containers\/[0-9a-f]{64}$/.test(url.pathname) && url.href !== first, {
      timeout: 90_000,
    });
    await expect(page.getByText(CONTAINER).first()).toBeVisible();

    // Deleted: stopped first; the page asks with the browser's confirm.
    const id = page.url().split("/").pop()!;
    expect((await page.request.post(`/api/admin/containers/${id}/stop`)).status()).toBe(204);
    await page.goto("/admin/containers");
    const row = page.getByRole("row", { name: new RegExp(CONTAINER) }).first();
    await row.waitFor();
    page.once("dialog", (d) => void d.accept());
    await row.getByRole("button", { name: "Delete" }).dispatchEvent("click");
    await expect(page.getByRole("row", { name: new RegExp(CONTAINER) })).toHaveCount(0, { timeout: 60_000 });
    await w.inspect();
    await w.report("admin: a container's life");
    expect(w.failures()).toEqual([]);
  });

  test("a skill is made on My skills, and removed", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/skills");
    await page.getByRole("button", { name: "Create Skill" }).click();
    const dialog = page.getByRole("dialog");
    const write = dialog.getByRole("button", { name: "Write it myself" });
    if (await write.count()) await write.click();
    await dialog.getByLabel(/Skill Name/).fill(`Sweep skill ${STAMP}`);
    await dialog.getByLabel("Description").fill("Made by the UI sweep; removed at its end.");
    // The instructions are required; their editor has no label, only a placeholder.
    await dialog.getByPlaceholder(/## Goal/).fill("## Goal\nSay that the sweep ran.");
    await dialog.getByRole("button", { name: "Save skill" }).click();
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    await expect(page.getByText(`Sweep skill ${STAMP}`).first()).toBeVisible();
    await w.inspect();
    await w.report("skills: made");

    const list = await (await page.request.get("/api/skills?limit=200")).json();
    const made = (list.items ?? []).find((s: { name?: string }) => s.name === `Sweep skill ${STAMP}`);
    expect(made, "the skill is listed").toBeTruthy();
    expect((await page.request.delete(`/api/skills/${made.id}`)).status()).toBeLessThan(300);
    expect(w.failures()).toEqual([]);
  });

  test("an MCP server answers its test", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    const servers = await (await page.request.get("/api/admin/mcp/servers")).json();
    const everything = servers.find((s: { name: string }) => s.name === "everything");
    test.skip(!everything, "no MCP server named everything on this install");
    await page.goto(`/admin/mcp/servers/${everything.id}`);
    await page.getByRole("button", { name: "Test" }).click();
    await expect(page.getByText(/reachable|active/i).first()).toBeVisible({ timeout: 60_000 });
    await w.inspect();
    await w.report("admin: MCP test");
    expect(w.failures()).toEqual([]);
  });

  test("the theme and the language switch, and switch back", async ({ page, baseURL }) => {
    const w = watch(page, baseURL);
    await page.goto("/admin/dashboard");
    await page.getByRole("button", { name: /Switch to light mode/ }).click();
    await expect(page.getByRole("button", { name: /Switch to dark mode/ })).toBeVisible();
    await page.getByRole("button", { name: /Switch to dark mode/ }).click();

    await page.getByRole("button", { name: "Language" }).click();
    await page.getByRole("menuitem", { name: /Deutsch/ }).click();
    await expect(page.getByText("Container").first()).toBeVisible();
    await w.inspect();
    await w.report("admin: in German");
    await page.getByRole("button", { name: /Sprache|Language/ }).click();
    await page.getByRole("menuitem", { name: /English/ }).click();
    await expect(page.getByText("Containers").first()).toBeVisible();
    expect(w.failures()).toEqual([]);
  });
});
