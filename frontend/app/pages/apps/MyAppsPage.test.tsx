/**
 * The people's Apps page: the offered apps, this server's address, how to
 * connect, and the empty state.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { appsApi, type OfferedApps } from "~/api/apps";
import { ApiError } from "~/api/http";
import { getAppTheme } from "~/theme";
import MyAppsPage from "./MyAppsPage";

const offered: OfferedApps = {
  server: "https://faden.example.org",
  vendor_links: false,
  apps: [
    {
      id: "chrome",
      name: "Fadenstack for Chrome",
      description: "Chat in the browser.",
      page: "https://fadenstack.com/apps/chrome",
      store: "https://chromewebstore.google.com/detail/fadenstack",
      connect: "chrome",
      agents: {},
      supported_until: "2027-01-31",
      latest: {
        version: "0.3.0",
        channel: "preview",
        date: "2026-11-01",
        severity: "normal",
        summary: "",
        notes_url: "",
        requires_server: null,
        fixes: [],
        files: [
          { platform: "chrome", kind: "crx", url: "https://fadenstack.com/dl/chrome.crx", sha256: "b".repeat(64), size: 2048 },
        ],
      },
    },
    {
      id: "nextcloud",
      name: "Fadenstack for Nextcloud",
      description: "",
      page: "",
      store: "",
      connect: null,
      agents: {},
      supported_until: null,
      latest: null,
    },
  ],
};

function view() {
  return render(
    <ThemeProvider theme={getAppTheme("light")}>
      <MemoryRouter initialEntries={["/apps"]}>
        <MyAppsPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("MyAppsPage", () => {
  it("lists the offered apps with this server's address and safe links", async () => {
    vi.spyOn(appsApi, "offered").mockResolvedValue(offered);
    view();

    const card = await screen.findByTestId("offered-chrome");
    expect(screen.getByTestId("apps-server")).toHaveTextContent("https://faden.example.org");
    expect(within(card).getByText("Fadenstack for Chrome")).toBeInTheDocument();
    expect(within(card).getByText("Preview")).toBeInTheDocument();
    expect(within(card).getByText("How to connect")).toBeInTheDocument();
    expect(within(card).getByText(/Install the extension/)).toBeInTheDocument();
    expect(card.querySelector("img")).toBeNull();

    // The vendor's page is left out (vendor_links false); the store and the file stay.
    const links = within(card).getAllByRole("link");
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link.getAttribute("rel") ?? "").toContain("noopener");
      expect(link.getAttribute("rel") ?? "").toContain("noreferrer");
      expect(link).toHaveAttribute("referrerpolicy", "no-referrer");
    }
    // An app with no way to connect shows only what it has.
    const bare = screen.getByTestId("offered-nextcloud");
    expect(within(bare).queryByText("How to connect")).toBeNull();
  });

  it("says when no app is offered yet", async () => {
    vi.spyOn(appsApi, "offered").mockResolvedValue({ ...offered, apps: [] });
    view();
    expect(await screen.findByTestId("apps-empty")).toHaveTextContent("No apps are offered here yet");
  });

  it("shows the error with a retry", async () => {
    vi.spyOn(appsApi, "offered").mockRejectedValue(new ApiError("Not allowed.", 403));
    view();
    expect(await screen.findByText("Not allowed.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument());
  });
});
