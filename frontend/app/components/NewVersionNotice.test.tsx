/**
 * After an upgrade an open tab moves to the new console by itself -- but never
 * over text someone has typed.
 *
 * With route discovery "initial" a page never asked the server again, so a
 * tab opened before an upgrade kept the old console until a forced reload.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { hasTypedText, serverRunsNewerBuild } from "~/lib/newBuild";
import NewVersionNotice from "./NewVersionNotice";

type ManifestWindow = { __reactRouterManifest?: { version: string } };

function serverRuns(version: string) {
  return vi.fn(() => Promise.resolve(Response.json({ version })));
}

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("serverRunsNewerBuild", () => {
  afterEach(() => {
    delete (window as ManifestWindow).__reactRouterManifest;
  });

  it("compares the page's build with the server's", async () => {
    (window as ManifestWindow).__reactRouterManifest = { version: "a5b04726" };
    expect(await serverRunsNewerBuild(serverRuns("a5b04726"))).toBe(false);
    expect(await serverRunsNewerBuild(serverRuns("c0ffee11"))).toBe(true);
  });

  it("cannot tell, so says no, outside a built page or when the server does not answer", async () => {
    expect(await serverRunsNewerBuild(serverRuns("c0ffee11"))).toBe(false);
    (window as ManifestWindow).__reactRouterManifest = { version: "a5b04726" };
    expect(await serverRunsNewerBuild(vi.fn(() => Promise.reject(new TypeError("offline"))))).toBe(false);
    expect(await serverRunsNewerBuild(vi.fn(() => Promise.resolve(new Response("", { status: 503 }))))).toBe(false);
  });
});

describe("hasTypedText", () => {
  it("sees typed text, not empty or disabled fields", () => {
    const box = document.createElement("div");
    box.innerHTML = '<textarea></textarea><input type="text" value=""><input type="checkbox" value="on">';
    expect(hasTypedText(box)).toBe(false);
    (box.querySelector("textarea") as HTMLTextAreaElement).value = "half a message";
    expect(hasTypedText(box)).toBe(true);
    (box.querySelector("textarea") as HTMLTextAreaElement).disabled = true;
    expect(hasTypedText(box)).toBe(false);
  });
});

describe("NewVersionNotice", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (window as ManifestWindow).__reactRouterManifest = { version: "old" };
    vi.spyOn(window, "fetch").mockImplementation(serverRuns("new"));
    setVisibility("visible");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete (window as ManifestWindow).__reactRouterManifest;
    document.body.innerHTML = "";
  });

  function view(reload: () => void) {
    const router = createMemoryRouter(
      [
        { path: "/a", element: <NewVersionNotice checkMs={1000} reload={reload} /> },
        { path: "/b", element: <NewVersionNotice checkMs={1000} reload={reload} /> },
      ],
      { initialEntries: ["/a"] },
    );
    render(<RouterProvider router={router} />);
    return router;
  }

  async function tick() {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  }

  it("offers the new console in front, and reloads at the next navigation", async () => {
    const reload = vi.fn();
    const router = view(reload);
    await tick();

    expect(screen.getByText("Fadenstack has been updated.")).toBeTruthy();
    expect(reload).not.toHaveBeenCalled();

    await act(async () => {
      await router.navigate("/b");
    });
    expect(reload).toHaveBeenCalled();
  });

  it("reloads with the notice's button", async () => {
    const reload = vi.fn();
    view(reload);
    await tick();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("reloads a tab in the background at once, but not over typed text", async () => {
    const reload = vi.fn();
    const draft = document.createElement("textarea");
    draft.value = "half a message";
    document.body.appendChild(draft);
    view(reload);

    setVisibility("hidden");
    await tick();
    expect(reload).not.toHaveBeenCalled();

    draft.value = "";
    setVisibility("visible");
    setVisibility("hidden");
    expect(reload).toHaveBeenCalled();
  });

  it("does not reload a navigation that carries a new chat's first message", async () => {
    const reload = vi.fn();
    const router = view(reload);
    await tick();
    await act(async () => {
      await router.navigate("/b", { state: { initialMessage: "hi" } });
    });
    expect(reload).not.toHaveBeenCalled();
  });
});
