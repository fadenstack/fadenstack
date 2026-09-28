/**
 * The screenshots in docs/images (and the README's), taken again on an installed server.
 *
 * Runs only with DOCS_SHOTS=1, so the sweep does not rewrite the docs on every run:
 *
 *   E2E_BASE_URL=https://<server> E2E_IGNORE_HTTPS_ERRORS=1 E2E_COOKIE=<session> DOCS_SHOTS=1 \
 *     npx playwright test -c playwright.sweep.config.ts e2e/sweep/50-docs-shots.spec.ts
 *
 * Looks, never changes the fleet: every dialog opened here is cancelled, and nothing is
 * scaled, deployed, routed or created -- except one chat, made for the chat pictures.
 * Expects what the docs describe: a cluster serving a deployment on two copies, a machine
 * with vLLM containers Fadenstack did not start, MCP servers and skills. A picture whose
 * state is missing is skipped, not faked.
 */
import { expect, test, type Page } from "@playwright/test";

const IMAGES = "../docs/images";
const DEPLOYMENT = process.env.DOCS_DEPLOYMENT ?? "qwen-chat";
const MACHINE_WITH_FOUND_VLLM = process.env.DOCS_FOUND_MACHINE ?? "10.250.10.71";
const CHAT_PROMPT = "Write a Python function that merges two sorted lists.";

test.skip(process.env.DOCS_SHOTS !== "1", "set DOCS_SHOTS=1 to retake the docs screenshots");

async function settle(page: Page, ms = 1500) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(ms);
}

async function shot(page: Page, ...paths: string[]) {
  for (const path of paths) await page.screenshot({ path: `${IMAGES}/${path}` });
}

test.describe("dashboard", () => {
  test("overview, customize and the light theme", async ({ page }) => {
    await page.goto("/admin/dashboard");
    await expect(page.getByText("Key metrics")).toBeVisible();
    await settle(page, 3500);
    await page.screenshot({ path: `${IMAGES}/dashboard/01-overview.png`, fullPage: true });
    await page.setViewportSize({ width: 1280, height: 800 });
    await settle(page, 800);
    await shot(page, "readme/dashboard.png");
    await page.setViewportSize({ width: 1280, height: 720 });

    await page.getByRole("button", { name: "Customize", exact: true }).filter({ visible: true }).first().click();
    await expect(page.getByRole("button", { name: "Done" })).toBeVisible();
    await settle(page, 800);
    await shot(page, "dashboard/02-customize.png");
    await page.getByRole("button", { name: "Done" }).click();

    await page.getByRole("button", { name: /Switch to light mode/ }).first().click();
    await settle(page, 1500);
    await shot(page, "dashboard/03-light-theme.png");
    await page.getByRole("button", { name: /Switch to dark mode/ }).first().click();
  });

  test("the PII dashboard", async ({ page }) => {
    await page.goto("/admin/pii/dashboard");
    await settle(page, 3000);
    await shot(page, "dashboard/04-pii-dashboard.png");
  });
});

test.describe("containers", () => {
  test("the list with its state tiles, and the networks", async ({ page }) => {
    await page.goto("/admin/containers");
    await expect(page.getByRole("group", { name: /Containers by state/ })).toBeVisible();
    await expect(page.getByText("faden-postgres").first()).toBeVisible();
    await settle(page);
    await shot(page, "dashboard/05-containers-tiles.png", "containers/01-containers-list.png");

    await page.goto("/admin/networks");
    await settle(page);
    await shot(page, "containers/11-networks.png");
  });
});

test.describe("MCP servers", () => {
  test("the list, the register dialog, a server and its container", async ({ page }) => {
    await page.goto("/admin/mcp/servers");
    await expect(page.getByText("everything").first()).toBeVisible();
    await settle(page);
    await shot(page, "mcp/04-servers-list.png");

    await page.getByRole("button", { name: "Register Server" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await settle(page, 600);
    await shot(page, "mcp/02-register-server.png");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();

    await page.getByText("everything", { exact: true }).first().click();
    await settle(page, 2000);
    await shot(page, "mcp/05-server-detail.png");

    await page.goto("/admin/containers");
    const container = page.getByText("mcp-everything", { exact: true }).first();
    if (await container.isVisible().catch(() => false)) {
      await container.click();
      await settle(page, 2000);
      await shot(page, "mcp/01-server-container.png");
    }
  });
});

test.describe("machines", () => {
  test("the list, the Add a machine panel and its token path", async ({ page }) => {
    await page.goto("/admin/nodes");
    await expect(page.getByText("healthy").first()).toBeVisible();
    await settle(page);
    await shot(page, "onboarding/04-machines-healthy.png", "https/05-machines-over-https.png");

    await page.getByRole("button", { name: "Add a machine" }).last().click();
    await expect(page.getByText("On the machine you want to add", { exact: false })).toBeVisible();
    await settle(page, 800);
    await shot(page, "onboarding/02-add-a-machine.png", "https/07-add-a-machine.png");

    // The token path, shown but not used: creating a token would mint a real credential.
    await page.getByRole("button", { name: "Skip the approval step" }).click();
    const create = page.getByRole("button", { name: "Create a token" });
    await expect(create).toBeVisible();
    await create.evaluate((el) => el.scrollIntoView({ block: "center" }));
    await settle(page, 500);
    await shot(page, "faq/skip-the-approval-step.png");
  });

  test("vLLM found on a machine, and routing one", async ({ page }) => {
    await page.goto("/admin/nodes");
    await page.getByText(MACHINE_WITH_FOUND_VLLM, { exact: true }).first().click();
    const found = page.getByTestId("found-vllm");
    await expect(found).toBeVisible({ timeout: 30_000 });
    await settle(page, 1500);
    await found.scrollIntoViewIfNeeded();
    await found.screenshot({ path: `${IMAGES}/found/found-on-machine.png` });

    // A stopped container's Route button is disabled; take the first one that is not.
    const route = page.locator('[data-testid^="found-route-"]:not([disabled])').first();
    if (await route.isVisible().catch(() => false)) {
      await route.click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await settle(page, 600);
      await dialog.screenshot({ path: `${IMAGES}/found/found-route-dialog.png` });
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    }
  });

  test("the logs of one machine", async ({ page }) => {
    await page.goto("/admin/logs");
    await page.getByText("Last 15m", { exact: true }).click();
    await page.getByRole("option", { name: /24/ }).click();
    const host = page.getByTestId("logs-filter-host");
    await expect(host).toBeVisible({ timeout: 30_000 });
    await host.click();
    await page.getByRole("option", { name: /gpu-01/ }).click();
    await settle(page, 3000);
    await shot(page, "logs/logs-one-machine.png");
  });
});

test.describe("cluster and deployment", () => {
  test("the cluster page, its Ray TLS switch and the deploy wizard", async ({ page }) => {
    await page.goto("/admin/clusters");
    const link = page.getByText(/^dgx-pair|^gpu-pair-/).first();
    const cluster = (await link.innerText()).trim();
    await link.click();
    await expect(page.getByRole("heading", { name: cluster })).toBeVisible();
    await settle(page, 2500);
    await shot(page, "https/18-model-traffic-encrypted.png");

    const rayTls = page.getByText(/between machines/i).first();
    if (await rayTls.isVisible().catch(() => false)) {
      const card = rayTls.locator("xpath=ancestor::div[contains(@class,'MuiCard') or contains(@class,'MuiPaper')][1]");
      await card.scrollIntoViewIfNeeded();
      await card.screenshot({ path: `${IMAGES}/https/19-ray-tls-card.png` });
    }

    // The wizard lists every model the server keeps; review that list before it goes into
    // the docs, then run with DOCS_DEPLOY_WIZARD=1.
    if (process.env.DOCS_DEPLOY_WIZARD === "1") {
      await page.getByRole("button", { name: "Deploy a model" }).first().click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("heading", { name: "Host a model" })).toBeVisible();
      await settle(page, 600);
      await dialog.screenshot({ path: `${IMAGES}/faq/deploy-model-picker.png` });
      await dialog.getByText("Qwen2.5-0.5B-Instruct", { exact: true }).click();
      await settle(page, 400);
      await shot(page, "onboarding/10-deploy-wizard.png");
      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toBeHidden();
    }
  });

  test("the deployment, serving on two copies, and its dialogs", async ({ page }) => {
    await page.goto("/admin/deployments");
    await page.getByText(DEPLOYMENT, { exact: true }).first().click();
    await expect(page.getByText("Copies (ready / wanted)")).toBeVisible();
    await settle(page, 2500);
    await shot(page, "onboarding/11-deployment.png", "onboarding/15-deployment-scaled.png", "upgrading/04-still-serving.png");

    await page.getByRole("button", { name: /^Scale$/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByLabel("Copies")).toBeVisible();
    await settle(page, 600);
    await shot(page, "onboarding/13-scale-dialog.png");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();

    const offer = page.getByRole("button", { name: /Offer in chat/ }).first();
    if (await offer.isVisible().catch(() => false)) {
      await offer.click();
      const offerDialog = page.getByRole("dialog");
      await expect(offerDialog).toBeVisible();
      await settle(page, 600);
      await shot(page, "faq/offer-in-chat-dialog.png");
      await page.keyboard.press("Escape");
    }
  });
});

test.describe("chat", () => {
  test("a chat on the cluster's model, and the chat's controls", async ({ page }) => {
    await page.goto("/chat");
    const search = page.getByPlaceholder("Search chats");
    // The select is named by its value; find it through its "Model" label.
    const model = page
      .locator(".MuiFormControl-root", { has: page.getByText("Model", { exact: true }) })
      .getByRole("combobox")
      .first();
    const composer = page.getByPlaceholder(/Type a message/);

    // Reuse the chat an earlier run made; make it only once.
    await search.fill("sorted");
    await settle(page, 1200);
    // The newest match, and never the older test chat of the same subject.
    const earlier = page
      .getByText(/sorted/i)
      .filter({ visible: true })
      .filter({ hasNotText: "Write a Python function to merge two sorted lists" })
      .first();
    if (await earlier.isVisible().catch(() => false)) {
      await earlier.click();
      await search.fill("");
      await expect(page.getByText(/def merge/).first()).toBeVisible({ timeout: 30_000 });
      await settle(page, 1500);
      await model.click();
      await settle(page, 400);
      await shot(page, "found/chat-picker.png");
      await page.keyboard.press("Escape");
    } else {
      await search.fill("");
      await page.getByRole("button", { name: /New chat/ }).first().click();
      await model.click();
      await settle(page, 400);
      await shot(page, "found/chat-picker.png");
      await page.getByRole("option", { name: new RegExp(DEPLOYMENT, "i") }).first().click();
      await composer.fill(CHAT_PROMPT);
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await expect(page.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0, { timeout: 120_000 });
      await settle(page, 4000); // the title arrives after the answer
    }
    await expect(page.getByText(/def merge/).first()).toBeVisible();
    await expect(page.getByText(/Sachith/)).toHaveCount(0);
    // Leave the picker on the cluster's model, so the composer shows it.
    await model.click();
    await page.getByRole("option", { name: new RegExp(DEPLOYMENT, "i") }).first().click();
    await expect(page.getByRole("listbox")).toHaveCount(0);
    // The question and the start of the answer, not the end of a long reply.
    await page.mouse.move(780, 400);
    await page.mouse.wheel(0, -5000);
    await settle(page, 800);
    await shot(page, "onboarding/12-chat.png");

    await search.fill("sorted");
    await settle(page, 1200);
    await shot(page, "chat/01-title-and-search.png");
    await search.fill("");
    await settle(page, 600);

    const bar = page.locator("header, .MuiToolbar-root").first();
    await bar.getByRole("button", { name: "Rename" }).click();
    const title = bar.getByRole("textbox").first();
    await title.fill("Merging sorted lists");
    await settle(page, 500);
    await shot(page, "chat/02-rename.png");
    await page.getByRole("button", { name: "Cancel", exact: true }).first().click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
    await settle(page, 500);

    await page.getByRole("button", { name: "Collapse" }).first().click();
    await settle(page, 800);
    await shot(page, "chat/03-collapsed-rail.png", "readme/chat.png");
    await page.getByRole("button", { name: "Expand" }).first().click();
    await settle(page, 800);

    await composer.fill("$test");
    await expect(page.getByText(/Test case generator/).first()).toBeVisible();
    await settle(page, 600);
    await shot(page, "chat/04-skill-picker.png");
    await composer.fill("");
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "Tools" }).first().click();
    await expect(page.getByText("everything").first()).toBeVisible();
    await settle(page, 600);
    await shot(page, "mcp/07-chat-tools.png");
    await page.keyboard.press("Escape");
  });
});
