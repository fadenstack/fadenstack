import { expect, test as setup } from "@playwright/test";

const SWEEP_STATE = "e2e/.auth/sweep.json";

/**
 * Sign in once for the sweep: with E2E_COOKIE (a console session cookie) on
 * an installed server, or with the dev-only login on a dev stack.
 */
setup("sign in", async ({ page, context, baseURL }) => {
  const cookie = process.env.E2E_COOKIE;
  if (cookie) {
    await context.addCookies([{ name: "fapiauth", value: cookie, url: baseURL! }]);
  } else {
    const response = await page.request.post("/api/auth/dev-login");
    expect(response.ok(), `dev-login failed (${response.status()}); set E2E_COOKIE for an installed server`).toBeTruthy();
  }
  await page.goto("/admin/dashboard");
  await expect(page).not.toHaveURL(/\/login/);
  await context.storageState({ path: SWEEP_STATE });
});
