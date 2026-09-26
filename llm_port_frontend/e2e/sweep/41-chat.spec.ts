/**
 * The chat, as a user works it: a reply, its title, renaming, searching,
 * projects, the tools and privacy panels, and skills.
 *
 * What it makes -- a chat, a project -- it deletes again.
 */
import { expect, test, type Page } from "@playwright/test";

import { Watch } from "./watch";

const STAMP = `${Date.now().toString(36)}`;

async function send(page: Page, text: string) {
  await page.getByPlaceholder(/Type a message/).fill(text);
  await page.getByRole("button", { name: "Send" }).click();
}

/** The reply is done: no Stop button, and the composer takes text again. */
async function replyDone(page: Page) {
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0, { timeout: 120_000 });
}

/** The chat's sidebar: the panel with the chat search (it is not a navigation landmark). */
function sidebar(page: Page) {
  return page.locator(".MuiDrawer-anchorLeft .MuiDrawer-paper").first();
}

/** The row of a sidebar entry, with the buttons it shows on hover. */
function rowOf(entry: ReturnType<Page["locator"]>) {
  return entry.locator("xpath=ancestor::div[contains(@class,'MuiListItemButton-root')][1]");
}

/** The sidebar row of the chat titled *title*. */
function chatRow(page: Page, title: string | RegExp) {
  return sidebar(page).getByText(title).first();
}

/** Click one of the buttons a chat's row shows on hover (a desktop; a phone has a menu). */
async function onRow(page: Page, title: string | RegExp, button: "Move to project" | "Delete") {
  const row = chatRow(page, title);
  await row.hover();
  await rowOf(row).getByRole("button", { name: button }).click();
}

test.describe("a chat", () => {
  test.describe.configure({ mode: "serial" });

  test("answers, is named from its first message, renamed, found, moved into a project and out, and deleted", async ({
    page,
    baseURL,
  }) => {
    const watch = new Watch(page, new URL(baseURL!).origin);
    const first = `Sweep ${STAMP}: name one colour of the sky`;
    await page.goto("/chat");
    await expect(sidebar(page).getByText("Today").first()).toBeVisible(); // the list has loaded
    await send(page, first);
    await page.waitForURL(/\/chat\/[0-9a-f-]{36}/);
    await replyDone(page);
    const assistant = page.locator("[data-role='assistant'], .assistant-message").first();
    if (await assistant.count()) await expect(assistant).not.toBeEmpty();

    // Named from the first message.
    await expect(chatRow(page, `Sweep ${STAMP}`)).toBeVisible({ timeout: 30_000 });

    // Renamed in place, in the chat's bar.
    const renamed = `Renamed ${STAMP}`;
    await page.locator("header, .MuiToolbar-root").first().getByRole("button", { name: "Rename" }).click();
    const title = page.locator(`input[value^="Sweep ${STAMP}"]`);
    await title.fill(renamed);
    await page.keyboard.press("Enter"); // the field no longer matches its old value
    await expect(chatRow(page, renamed)).toBeVisible();
    await page.reload();
    await expect(chatRow(page, renamed)).toBeVisible();

    // Found by the search.
    const search = page.getByPlaceholder("Search chats");
    await search.fill(renamed);
    await expect(chatRow(page, renamed)).toBeVisible();
    await search.fill(`nothing-${STAMP}-matches`);
    await expect(sidebar(page).getByText(renamed)).toHaveCount(0);
    await search.fill("");

    // Into a new project and out again; each move is kept.
    const project = `Sweep project ${STAMP}`;
    await page.getByRole("button", { name: "New project" }).click();
    await page.getByPlaceholder("Project name").fill(project);
    await page.getByPlaceholder("Project name").press("Enter");
    await expect(sidebar(page).getByText(project)).toBeVisible();
    await onRow(page, renamed, "Move to project");
    await page.getByRole("menuitem", { name: project }).click();
    await page.reload();
    await expect(chatRow(page, renamed)).toBeVisible();
    await onRow(page, renamed, "Move to project");
    await page.getByRole("menuitem", { name: "No project" }).click();
    await page.reload();
    await expect(chatRow(page, renamed)).toBeVisible();

    // Deleted.
    await onRow(page, renamed, "Delete");
    const confirm = page.getByRole("dialog").getByRole("button", { name: /delete/i });
    if (await confirm.count()) await confirm.click();
    await expect(sidebar(page).getByText(renamed)).toHaveCount(0);

    // The project too.
    const projectRow = sidebar(page).getByText(project).first();
    await projectRow.hover();
    await rowOf(projectRow).getByRole("button", { name: "Delete" }).click();
    const confirmProject = page.getByRole("dialog").getByRole("button", { name: /delete/i });
    if (await confirmProject.count()) await confirmProject.click();
    await expect(sidebar(page).getByText(project)).toHaveCount(0);

    await watch.inspect();
    await watch.report("chat: the whole flow");
    expect(watch.failures()).toEqual([]);
  });

  test("started before the chat list has loaded, stays in the sidebar with its title", async ({ page, baseURL }) => {
    // The list replaced the chat started meanwhile: gone from the sidebar, "Untitled chat" in the bar.
    const watch = new Watch(page, new URL(baseURL!).origin);
    await page.route(/\/api\/chat\/sessions(\?.*)?$/, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      // Answered now, delivered late: the list as it was before the chat existed.
      const response = await route.fetch();
      await new Promise((r) => setTimeout(r, 3_000));
      await route.fulfill({ response });
    });
    const text = `Early ${STAMP}: say ok`;
    await page.goto("/chat");
    await send(page, text);
    await page.waitForURL(/\/chat\/[0-9a-f-]{36}/);
    await replyDone(page);
    await page.waitForTimeout(4_000); // the list arrives after the reply
    await expect(chatRow(page, `Early ${STAMP}`)).toBeVisible();
    await expect(page.locator("header, .MuiToolbar-root").first()).toContainText(`Early ${STAMP}`);
    await watch.report("chat: started before the list loaded");
    await page.request.delete(`/api/chat/sessions/${page.url().split("/chat/")[1]}`);
  });

  test("shows its tools, switched off, and its privacy settings", async ({ page, baseURL }) => {
    const watch = new Watch(page, new URL(baseURL!).origin);
    await page.goto("/chat");
    await page.getByRole("button", { name: "Tools" }).first().click();
    await expect(page.getByText(/The model can find and switch on the others/)).toBeVisible();
    await expect(page.getByText("everything").first()).toBeVisible();
    await page.getByRole("tab", { name: "Privacy" }).click();
    await page.waitForTimeout(800);
    await watch.inspect();
    await watch.report("chat: tools and privacy panels");
    expect(watch.failures()).toEqual([]);
  });

  test("offers the skills with $ and answers with one picked", async ({ page, baseURL }) => {
    const watch = new Watch(page, new URL(baseURL!).origin);
    await page.goto("/chat");
    const composer = page.getByPlaceholder(/Type a message/);
    await composer.click();
    await composer.pressSequentially("$bug", { delay: 50 }); // typed: the picker follows the keys
    const option = page.getByRole("option", { name: /Bug report triage/ }).or(
      page.getByRole("menuitem", { name: /Bug report triage/ }),
    );
    await expect(option.first()).toBeVisible();
    await option.first().click();
    await expect(page.getByText(/Bug report triage/).first()).toBeVisible();
    await composer.fill("The export button shows an error since yesterday.");
    await page.getByRole("button", { name: "Send" }).click();
    await page.waitForURL(/\/chat\/[0-9a-f-]{36}/);
    await replyDone(page);
    await watch.inspect();
    await watch.report("chat: a picked skill");

    // Tidy: the chat goes again.
    const sid = page.url().split("/chat/")[1];
    await page.request.delete(`/api/chat/sessions/${sid}`);
    expect(watch.failures()).toEqual([]);
  });
});
