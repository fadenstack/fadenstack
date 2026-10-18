/**
 * About Fadenstack, the user menu's Help and About, the sidebar's version line and the sign-in footer: where the
 * links go and what they carry (fadenstack-dev features/about-and-ref-links.md).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as about from "~/api/about";
import { renderPage } from "~/test/renderPage";
import Login from "~/routes/login";

import { AboutDialog, AboutVersionLine } from "./AboutDialog";
import { UserMenuButton } from "./UserMenuButton";
import { AdminTopbar } from "./AdminTopbar";

vi.mock("~/api/about", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/about")>();
  return { ...actual, aboutInfo: vi.fn() };
});

const aboutInfo = vi.mocked(about.aboutInfo);
const CORE = { product: "Fadenstack", edition: "core", version: "0.4.5", licence: "Apache-2.0", vendor_links: true };

function link(dialog: HTMLElement, key: string): HTMLAnchorElement | null {
  return dialog.querySelector(`a[data-link="${key}"]`);
}

describe("About Fadenstack", () => {
  beforeEach(() => {
    aboutInfo.mockReset();
  });

  it("names the edition, release and licence, and tags every link to fadenstack.com", async () => {
    aboutInfo.mockResolvedValue(CORE);
    render(<AboutDialog open onClose={() => {}} />);
    const dialog = await screen.findByTestId("about-dialog");
    await waitFor(() => expect(within(dialog).getByTestId("about-version")).toHaveTextContent("0.4.5"));
    expect(dialog).toHaveTextContent("Core");
    expect(dialog).toHaveTextContent("Apache-2.0");

    expect(link(dialog, "releases")?.href).toBe("https://fadenstack.com/releases/?ref=console-about&v=0.4");
    expect(link(dialog, "website")?.href).toBe("https://fadenstack.com/?ref=console-about&v=0.4");
    expect(link(dialog, "security")?.href).toBe("https://fadenstack.com/security/?ref=console-about&v=0.4");
    expect(link(dialog, "developers")?.href).toBe("https://developers.fadenstack.com/?ref=console-about&v=0.4");
    expect(link(dialog, "source")?.href).toBe("https://github.com/fadenstack/fadenstack");
    for (const key of ["releases", "website", "security", "developers", "source"]) {
      expect(link(dialog, key)?.rel).toBe("noopener noreferrer");
      expect(link(dialog, key)?.target).toBe("_blank");
      expect(link(dialog, key)?.getAttribute("aria-label")).toContain("(opens in a new tab)");
    }
    // This server's own pages, untagged.
    expect(link(dialog, "help")?.getAttribute("href")).toBe("/help/");
    expect(link(dialog, "third-party")?.getAttribute("href")).toBe("/help/operate/third-party/");
  });

  it("keeps Help and the notices when an edition switches the vendor's links off", async () => {
    aboutInfo.mockResolvedValue({ ...CORE, edition: "enterprise", licence: "", vendor_links: false });
    render(<AboutDialog open onClose={() => {}} />);
    const dialog = await screen.findByTestId("about-dialog");
    await waitFor(() => expect(dialog).toHaveTextContent("Enterprise"));
    expect(link(dialog, "help")).not.toBeNull();
    expect(link(dialog, "third-party")).not.toBeNull();
    expect(dialog.querySelector('a[href*="fadenstack.com"]')).toBeNull();
  });

  it("says an install built from source has no release", async () => {
    aboutInfo.mockResolvedValue({ ...CORE, version: "" });
    render(<AboutDialog open onClose={() => {}} />);
    const dialog = await screen.findByTestId("about-dialog");
    await waitFor(() => expect(within(dialog).getByTestId("about-version")).toHaveTextContent("built from source"));
    expect(link(dialog, "website")?.href).toBe("https://fadenstack.com/?ref=console-about");
  });

  it("is in the user menu, with this server's Help, in the console and the chat", async () => {
    aboutInfo.mockResolvedValue(CORE);
    render(<UserMenuButton email="anna@example.com" onProfile={() => {}} onLogout={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /anna@example.com/ }));
    const help = await screen.findByRole("menuitem", { name: "Help" });
    expect(help.getAttribute("href")).toBe("/help/");
    fireEvent.click(screen.getByRole("menuitem", { name: "About Fadenstack" }));
    expect(await screen.findByTestId("about-dialog")).toBeInTheDocument();
  });

  it("is opened by the sidebar's muted release line, which leaves the page for nothing", async () => {
    aboutInfo.mockResolvedValue(CORE);
    render(<AboutVersionLine />);
    const line = await screen.findByTestId("about-version-line");
    await waitFor(() => expect(line).toHaveTextContent("Fadenstack 0.4.5"));
    expect(line.tagName).toBe("BUTTON");
    fireEvent.click(line);
    expect(await screen.findByTestId("about-dialog")).toBeInTheDocument();
  });
});

describe("the sign-in page", () => {
  it("links to fadenstack.com without a version, and to this server's Help", async () => {
    aboutInfo.mockReset();
    renderPage(<Login />, { path: "/login" });
    const footer = await screen.findByTestId("signin-footer");
    const [site, help] = within(footer).getAllByRole("link");
    expect(site.getAttribute("href")).toBe("https://fadenstack.com/?ref=console-signin");
    expect(site.getAttribute("rel")).toBe("noopener noreferrer");
    expect(help.getAttribute("href")).toBe("/help/");
    expect(aboutInfo).not.toHaveBeenCalled(); // nothing asked before signing in
  });
});

describe("the console's Help menu", () => {
  it("ends with About Fadenstack", async () => {
    aboutInfo.mockResolvedValue(CORE);
    const noop = () => {};
    renderPage(
      <AdminTopbar
        mode="light" toggleMode={noop} currentUserEmail="admin@example.com" isSuperuser rootStatus={null}
        languages={[]} language="en" languageMenuAnchor={null} onLanguageMenuOpen={noop} onLanguageMenuClose={noop}
        onLanguageChange={noop} onGettingStartedOpen={noop} onConsoleTourOpen={noop} onHowToGuidesOpen={noop}
        onResetGuides={noop} onPageHelp={noop} onRootFormOpen={noop} onRootDeactivate={noop} onLogout={noop}
      />,
    );
    fireEvent.click(document.querySelector('[data-tour-id="topbar.help"]') ?? screen.getAllByRole("button")[0]);
    const items = await screen.findAllByRole("menuitem");
    expect(items.at(-1)).toHaveTextContent("About Fadenstack");
    fireEvent.click(items.at(-1)!);
    expect(await screen.findByTestId("about-dialog")).toBeInTheDocument();
  });
});
