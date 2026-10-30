/** My Profile → Signed-in apps: the list, the empty state, signing out (after asking) and the errors. */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import * as agents from "~/api/agents";
import { renderPage } from "~/test/renderPage";

import { AppSignInsCard } from "./AppSignInsCard";

const sign = (over: Partial<agents.AgentSignIn> = {}): agents.AgentSignIn => ({
  device_id: "d1", agent_id: "a1", user_id: "u1", device_label: "Ada's laptop",
  created_at: "2026-10-01T09:00:00Z", last_used_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
  expires_at: "2027-01-01T00:00:00Z", idle_expires_at: "2026-12-01T00:00:00Z", ...over,
});

function given(signIns: agents.AgentSignIn[]) {
  vi.spyOn(agents.agentsApi, "list").mockResolvedValue([{ id: "a1", name: "Desktop agent" } as agents.Agent]);
  return vi.spyOn(agents, "mySignIns").mockResolvedValue(signIns);
}

describe("signed-in apps on My Profile", () => {
  it("lists app, device, signed-in date and last use", async () => {
    given([sign(), sign({ device_id: "d2", device_label: null, last_used_at: null })]);
    renderPage(<AppSignInsCard />);
    expect(await screen.findByText("Ada's laptop")).toBeInTheDocument();
    expect(screen.getAllByText("Desktop agent")).toHaveLength(2);
    expect(screen.getByText("unnamed device")).toBeInTheDocument();
    expect(screen.getByText("not used yet")).toBeInTheDocument();
    expect(screen.getByText(/3 hr\. ago|3 hours ago/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Sign out" })).toHaveLength(2);
  });

  it("says when no app is signed in", async () => {
    given([]);
    renderPage(<AppSignInsCard />);
    expect(await screen.findByText("No apps are signed in.")).toBeInTheDocument();
  });

  it("signs out the right device after asking, then reloads the list", async () => {
    const mine = given([sign(), sign({ device_id: "d2", device_label: "Office PC" })]);
    const revoke = vi.spyOn(agents, "revokeSignIn").mockResolvedValue(undefined);
    renderPage(<AppSignInsCard />);
    await screen.findByText("Office PC");
    fireEvent.click(screen.getAllByRole("button", { name: "Sign out" })[1]);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Sign out Desktop agent on Office PC? The app must be signed in again.");
    mine.mockResolvedValue([sign()]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("d2"));
    await waitFor(() => expect(screen.queryByText("Office PC")).not.toBeInTheDocument());
    expect(mine).toHaveBeenCalledTimes(2);
  });

  it("says when the list cannot be loaded", async () => {
    vi.spyOn(agents.agentsApi, "list").mockResolvedValue([]);
    vi.spyOn(agents, "mySignIns").mockRejectedValue(new Error("boom"));
    renderPage(<AppSignInsCard />);
    expect(await screen.findByText("The signed-in apps could not be loaded.")).toBeInTheDocument();
  });

  it("says when signing out failed", async () => {
    given([sign()]);
    vi.spyOn(agents, "revokeSignIn").mockRejectedValue(new Error("nope"));
    renderPage(<AppSignInsCard />);
    await screen.findByText("Ada's laptop");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("Signing out failed.")).toBeInTheDocument();
  });

  it("has every text in the console's five languages, formally addressed", () => {
    const i18n = resolve("../backend/i18n");
    const ns = (lang: string) =>
      (JSON.parse(readFileSync(join(i18n, lang, "common.json"), "utf-8")) as { app_sign_ins: Record<string, string> })
        .app_sign_ins;
    const english = Object.keys(ns("en"));
    expect(english.length).toBeGreaterThan(10);
    const formal: Record<string, RegExp> = { de: /\bSie\b/, es: /\busted\b/, fr: /\bvous\b/, zh: /您/ };
    for (const lang of ["de", "es", "fr", "zh"]) {
      const texts = ns(lang);
      expect(Object.keys(texts)).toEqual(english);
      expect(Object.values(texts).every((v) => v.trim() !== "")).toBe(true);
      expect(Object.values(texts).join(" ")).toMatch(formal[lang]);
      expect(texts.confirm).toContain("{{agent}}");
      expect(texts.confirm).toContain("{{device}}");
    }
  });
});
