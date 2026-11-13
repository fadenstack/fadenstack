/**
 * The sign-in page's developer sign-in follows the console's own build: a development build offers it, a production
 * build never does. It no longer asks the anonymous health check, which says nothing about the install
 * (sdk/51 F01; the server refuses the developer sign-in outside development anyway).
 */
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import { auth } from "~/api/auth";
import { renderPage } from "~/test/renderPage";

import Login from "./login";

let fetched: string[];

beforeEach(() => {
  vi.spyOn(admin.adminAuthProviders, "listPublic").mockResolvedValue([]);
  vi.spyOn(auth, "me").mockRejectedValue(new Error("not signed in"));
  fetched = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    fetched.push(String(input));
    return new Response(JSON.stringify({ status: "ok" }), { headers: { "content-type": "application/json" } });
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the developer sign-in", () => {
  it("is offered in a development build, with the developer account filled in", async () => {
    vi.stubEnv("DEV", true);
    renderPage(<Login />, { path: "/login" });
    expect(await screen.findByRole("button", { name: "Dev Login" })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Email/)).toHaveValue("admin@localhost");
    expect(fetched).not.toContain("/api/health");
  });

  it("is never offered in a production build", async () => {
    vi.stubEnv("DEV", false);
    renderPage(<Login />, { path: "/login" });
    await screen.findByRole("button", { name: "Sign In" });
    await waitFor(() => expect(auth.me).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Dev Login" })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Email/)).toHaveValue("");
    expect(fetched).not.toContain("/api/health");
  });
});
