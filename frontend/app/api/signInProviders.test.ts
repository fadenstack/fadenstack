/** The sign-in provider API (signInProviders.ts): where each call goes, and how a refusal reads. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProviderApiError, signInProviders } from "./signInProviders";

function answer(status: number, body?: unknown) {
  // A fresh answer each call: a body is read once.
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(body === undefined ? null : JSON.stringify(body), { status }),
  );
}

afterEach(() => vi.restoreAllMocks());

describe("calls", () => {
  it("lists, creates, changes and deletes under /api/admin/auth-providers", async () => {
    const fetch = answer(200, []);
    await signInProviders.list();
    expect(fetch).toHaveBeenLastCalledWith("/api/admin/auth-providers/", expect.objectContaining({ credentials: "include" }));

    await signInProviders.create({ key: "corp", name: "Corp", enabled: true, settings: { protocol: "oidc" }, policy: {} as never });
    expect(fetch.mock.lastCall?.[0]).toBe("/api/admin/auth-providers/");
    expect(fetch.mock.lastCall?.[1]).toMatchObject({ method: "POST" });

    await signInProviders.update("a/b", { name: "Corp", enabled: true, settings: { protocol: "oidc" }, policy: {} as never });
    expect(fetch.mock.lastCall?.[0]).toBe("/api/admin/auth-providers/a%2Fb");
    expect(fetch.mock.lastCall?.[1]).toMatchObject({ method: "PATCH" });

    answer(204);
    await expect(signInProviders.remove("corp")).resolves.toBeUndefined();
  });
});

describe("refusals", () => {
  it("knows when no sign-in module is switched on", async () => {
    answer(404, { detail: "No sign-in module is switched on" });
    const err = await signInProviders.list().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProviderApiError);
    expect((err as ProviderApiError).moduleOff).toBe(true);
    expect(new ProviderApiError(404, "No such provider").moduleOff).toBe(false);
  });

  it("gives the module's validation messages, one per field", () => {
    const err = new ProviderApiError(422, [
      { loc: ["body", "settings", "oidc", "issuer"], msg: "an absolute http(s) URL is required" },
      { loc: ["body"], msg: "the service account needs its password (bind_password)" },
    ]);
    expect(err.messages).toEqual([
      "settings.oidc.issuer: an absolute http(s) URL is required",
      "the service account needs its password (bind_password)",
    ]);
    expect(new ProviderApiError(409, "A provider with this key exists").messages).toEqual(["A provider with this key exists"]);
    expect(new ProviderApiError(500, { odd: true }).messages).toEqual([]);
  });
});
