/**
 * The Updates page: what this server runs, the newer releases and how to
 * install them, every part of the install, "Check now", and the release list
 * uploaded by hand on a server without internet.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as about from "~/api/about";
import { ApiError } from "~/api/http";
import {
  updatesApi,
  type AppEntry,
  type AppRelease,
  type AppsOverview,
  type ReleaseInfo,
  type UpdatesOverview,
} from "~/api/updates";
import { clearDashboardQueries } from "~/lib/dashboardQuery";
import { getAppTheme } from "~/theme";
import UpdatesPage, { overviewStatus } from "./UpdatesPage";

const release: ReleaseInfo = {
  version: "0.4.2",
  channel: "stable",
  date: "2026-10-20",
  severity: "security",
  summary: "Security fixes for the gateway.",
  notes_url: "https://github.com/fadenstack/fadenstack/releases/tag/v0.4.2",
  upgrade_from: "0.4.0",
  fixes: [{ id: "FSA-2026-001", severity: "high", summary: "A token could be read from the logs." }],
  components: { cli: "0.4.2", agent: "0.2.8" },
};

function overview(overrides: Partial<UpdatesOverview> = {}): UpdatesOverview {
  return {
    check: {
      enabled: true,
      channel: "stable",
      feed_url: "https://fadenstack.com/updates/v1/index.json",
      source: "url",
      checked_at: "2026-10-21T12:00:00Z",
      succeeded_at: "2026-10-21T12:00:00Z",
      feed_generated_at: "2026-10-20T10:00:00Z",
      error: null,
      failing: false,
    },
    current: { release: "0.4.1", backend: "0.4.1", installer: "pipx" },
    latest: "0.4.2",
    severity: "security",
    update_available: true,
    unsupported: false,
    supported_until: null,
    latest_release: release,
    newer: [release],
    path: ["0.4.2"],
    commands: ["pipx upgrade fadenstack", "faden upgrade --dry-run", "faden upgrade -y"],
    released_agent: "0.2.8",
    parts: [
      {
        kind: "service",
        name: "faden-backend",
        running: "0.4.1",
        expected: "0.4.1",
        status: "current",
        detail: "ghcr.io/fadenstack/backend:0.4.1 (57bc1e09dead)",
        link: "/admin/containers",
      },
      {
        kind: "agent",
        name: "192.0.2.71",
        running: "0.2.6",
        expected: "0.2.7",
        status: "update",
        detail: null,
        link: "/admin/nodes/5b1c",
      },
      { kind: "cli", name: "faden", running: "0.4.1", expected: "0.4.1", status: "current", detail: "pipx", link: null },
    ],
    docker_error: null,
    ...overrides,
  };
}

vi.mock("~/api/about", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/about")>();
  return { ...actual, aboutInfo: vi.fn() };
});

function view() {
  return render(
    <ThemeProvider theme={getAppTheme("light")}>
      <MemoryRouter initialEntries={["/admin/updates"]}>
        <UpdatesPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const appRelease: AppRelease = {
  version: "1.4.0",
  channel: "stable",
  date: "2026-11-01",
  severity: "security",
  summary: "Fixes a sign-in problem.",
  notes_url: "https://fadenstack.com/apps/desktop/notes",
  requires_server: null,
  fixes: [],
  files: [
    {
      platform: "windows",
      kind: "msi",
      url: "https://fadenstack.com/dl/desktop-1.4.0.msi",
      sha256: "a".repeat(64),
      size: 52_428_800,
    },
  ],
};

function appsOverview(apps: AppEntry[] = [], overrides: Partial<AppsOverview> = {}): AppsOverview {
  return {
    check: {
      enabled: true,
      url: "https://fadenstack.com/updates/v1/apps.json",
      source: "url",
      checked_at: "2026-11-02T12:00:00Z",
      succeeded_at: "2026-11-02T12:00:00Z",
      list_generated_at: "2026-11-01T10:00:00Z",
      expires: "2026-11-09T10:00:00Z",
      expired: false,
      error: null,
    },
    news: true,
    in_use_days: 30,
    in_use_known: true,
    vendor_links: true,
    own_ca: false,
    apps,
    ...overrides,
  };
}

function desktopApp(overrides: Partial<AppEntry> = {}): AppEntry {
  return {
    id: "desktop",
    name: "Fadenstack Desktop",
    description: "The desktop agent.",
    page: "https://fadenstack.com/apps/desktop",
    store: "https://store.example.com/desktop",
    connect: "desktop",
    agents: {},
    offered: false,
    new: true,
    latest: appRelease,
    in_use: [{ version: "1.2.0", last_seen: "2026-11-01T08:00:00Z" }],
    outdated: ["1.2.0"],
    severity: "security",
    newer: [appRelease],
    unsupported: [],
    supported_until: "2027-06-30",
    server_too_old: false,
    connect_files: ["office-policy"],
    ...overrides,
  };
}

beforeEach(() => {
  vi.spyOn(updatesApi, "apps").mockResolvedValue(appsOverview());
  vi.mocked(about.aboutInfo).mockRejectedValue(new Error("not asked"));
  vi.spyOn(updatesApi, "notice").mockResolvedValue({
    current: "0.4.1",
    latest: "0.4.2",
    severity: "security",
    update_available: true,
    unsupported: false,
    supported_until: null,
  });
});

afterEach(() => {
  clearDashboardQueries();
  vi.restoreAllMocks();
});

describe("overviewStatus", () => {
  it("leads with the most urgent thing", () => {
    expect(overviewStatus(overview()).key).toBe("security");
    expect(overviewStatus(overview({ severity: "critical" })).key).toBe("critical");
    expect(overviewStatus(overview({ severity: "normal" })).key).toBe("update");
    expect(overviewStatus(overview({ update_available: false, newer: [], severity: "none" })).key).toBe("up_to_date");
    const never = overview({ update_available: false, newer: [], latest: null, latest_release: null, severity: "none" });
    never.check = { ...never.check, feed_generated_at: null, checked_at: null, succeeded_at: null };
    expect(overviewStatus(never).key).toBe("not_checked");
  });
});

describe("UpdatesPage", () => {
  it("shows the release, how to upgrade, the newer releases and the parts", async () => {
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview());
    view();

    expect(await screen.findByText("This server runs Fadenstack 0.4.1")).toBeInTheDocument();
    expect(screen.getByTestId("update-status")).toHaveTextContent("Security update");
    expect(screen.getByTestId("upgrade-commands")).toHaveTextContent("pipx upgrade fadenstack");
    expect(screen.getByTestId("upgrade-commands")).toHaveTextContent("faden upgrade -y");
    // One step only: no "one at a time".
    expect(screen.queryByTestId("upgrade-steps")).toBeNull();
    expect(screen.getByText(/machines show an agent update to 0.2.8/)).toBeInTheDocument();

    const newer = screen.getByRole("region", { name: "Newer releases" });
    expect(within(newer).getByText("FSA-2026-001")).toBeInTheDocument();
    expect(within(newer).getByRole("link", { name: /^Release notes/ })).toHaveAttribute("href", release.notes_url);

    const parts = screen.getByRole("region", { name: "Parts of this install" });
    expect(within(parts).getByRole("link", { name: "192.0.2.71" })).toHaveAttribute("href", "/admin/nodes/5b1c");
    expect(within(parts).getByText("Machine agent")).toBeInTheDocument();
    expect(within(parts).getByText("ghcr.io/fadenstack/backend:0.4.1 (57bc1e09dead)")).toBeInTheDocument();
  });

  it("says releases go one at a time when the path has several steps", async () => {
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview({ path: ["0.5.0", "0.6.0"] }));
    view();
    expect(await screen.findByTestId("upgrade-steps")).toHaveTextContent("0.4.1 → 0.5.0 → 0.6.0");
  });

  it("checks now and shows what came back, with the hint when the check keeps failing", async () => {
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview());
    const failed = overview();
    failed.check = { ...failed.check, error: "fadenstack.com did not answer within 10 s.", failing: true };
    vi.spyOn(updatesApi, "check").mockResolvedValue(failed);
    view();

    fireEvent.click(await screen.findByRole("button", { name: "Check now" }));
    const alert = await screen.findByTestId("check-error");
    expect(alert).toHaveTextContent("The last check failed: fadenstack.com did not answer within 10 s.");
    expect(alert).toHaveTextContent(/turn the check off/);
    expect(within(alert).getByRole("link", { name: "Open settings" })).toHaveAttribute(
      "href",
      "/admin/settings?tab=general",
    );
    // The banner and the sidebar's dot read the notice again.
    await waitFor(() => expect(updatesApi.notice).toHaveBeenCalledTimes(2));
  });

  it("says when the daily check is off, and shows no upgrade card when nothing is newer", async () => {
    const quiet = overview({ update_available: false, newer: [], severity: "none", path: [], commands: [] });
    quiet.check = { ...quiet.check, enabled: false };
    vi.spyOn(updatesApi, "overview").mockResolvedValue(quiet);
    view();

    expect(await screen.findByTestId("check-off")).toHaveTextContent("The daily check is off");
    expect(screen.getByTestId("update-status")).toHaveTextContent("Up to date");
    expect(screen.queryByRole("region", { name: "How to upgrade" })).toBeNull();
  });

  it("uploads a release list copied by hand and shows why it was refused", async () => {
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview());
    vi.spyOn(updatesApi, "uploadFeed").mockRejectedValue(
      new ApiError("The signature does not match the release list.", 422),
    );
    view();
    await screen.findByText("This server runs Fadenstack 0.4.1");

    fireEvent.click(screen.getByText("No internet on this server?"));
    const upload = await screen.findByRole("button", { name: "Upload" });
    expect(upload).toBeDisabled();

    const index = new File(["{}"], "index.json", { type: "application/json" });
    const signature = new File(["sig"], "index.json.sig");
    fireEvent.change(screen.getByLabelText("Choose index.json"), { target: { files: [index] } });
    fireEvent.change(screen.getByLabelText("Choose index.json.sig"), { target: { files: [signature] } });
    expect(upload).toBeEnabled();

    fireEvent.click(upload);
    expect(await screen.findByTestId("upload-error")).toHaveTextContent(
      "The signature does not match the release list.",
    );
    expect(updatesApi.uploadFeed).toHaveBeenCalledWith(index, signature);
  });

  it("shows the error and a retry when the page cannot load", async () => {
    vi.spyOn(updatesApi, "overview").mockRejectedValue(new ApiError("Not allowed.", 403));
    view();
    expect(await screen.findByText("Not allowed.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows the apps with a security alert, safe links, and offers an app to people", async () => {
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview());
    vi.spyOn(updatesApi, "apps").mockResolvedValue(appsOverview([desktopApp()], { news: false, in_use_known: false }));
    const put = vi
      .spyOn(updatesApi, "setAppOffered")
      .mockResolvedValue(appsOverview([desktopApp({ offered: true })]));
    view();

    const item = await screen.findByTestId("app-desktop");
    expect(within(item).getByText("Fadenstack Desktop")).toBeInTheDocument();
    expect(within(item).getByText("New")).toBeInTheDocument();
    expect(within(item).getByText("In use here")).toBeInTheDocument();
    expect(screen.getByTestId("app-alert-desktop")).toHaveTextContent(
      "Versions in use here have security problems fixed in 1.4.0. Have people install it.",
    );
    expect(screen.getByTestId("apps-news-off")).toBeInTheDocument();
    expect(screen.getByTestId("apps-in-use-unknown")).toBeInTheDocument();
    expect(item.querySelector("img")).toBeNull();

    const links = within(item).getAllByRole("link");
    expect(links.length).toBeGreaterThanOrEqual(4);
    for (const link of links) {
      expect(link.getAttribute("rel") ?? "").toContain("noreferrer");
      expect(link).toHaveAttribute("referrerpolicy", "no-referrer");
      expect(link).toHaveAttribute("target", "_blank");
    }

    fireEvent.click(within(item).getByRole("checkbox", { name: /Offer to people/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith("desktop", true, expect.any(String)));
    await waitFor(() => expect(within(screen.getByTestId("app-desktop")).getByRole("checkbox")).toBeChecked());
  });

  it("warns when the app list has expired, and shows its end date quietly while valid", async () => {
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview());
    const valid = appsOverview([desktopApp()]);
    const expired = appsOverview([desktopApp()]);
    expired.check = { ...expired.check, expired: true };
    const spy = vi.spyOn(updatesApi, "apps").mockResolvedValue(valid);
    view();
    expect(await screen.findByTestId("apps-status")).toHaveTextContent(/valid until/);
    expect(screen.queryByTestId("apps-expired")).toBeNull();

    spy.mockResolvedValue(expired);
    fireEvent.click(screen.getByTestId("apps-check-now"));
    const alert = await screen.findByTestId("apps-expired");
    expect(alert).toHaveTextContent("The app list has not been renewed since");
    expect(alert).toHaveTextContent("security fixes included");
  });

  it("opens an app's connect file with its steps", async () => {
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview());
    vi.spyOn(updatesApi, "apps").mockResolvedValue(appsOverview([desktopApp()], { own_ca: true }));
    vi.spyOn(updatesApi, "connectFile").mockResolvedValue('{"server":"https://faden.example"}');
    view();

    fireEvent.click(await screen.findByRole("button", { name: "Office policy file" }));
    expect(await screen.findByTestId("connect-file")).toHaveTextContent('{"server":"https://faden.example"}');
    expect(screen.getByTestId("connect-ca")).toHaveTextContent("fadenstack-ca.crt");
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/admin/updates/apps/desktop/connect/office-policy",
    );
  });

  it("leaves out the release notes where the server switched the vendor's links off", async () => {
    vi.mocked(about.aboutInfo).mockResolvedValue({
      product: "Fadenstack", edition: "enterprise", version: "0.4.1", licence: "", vendor_links: false,
    });
    vi.spyOn(updatesApi, "overview").mockResolvedValue(overview());
    view();
    const newer = await screen.findByRole("region", { name: "Newer releases" });
    expect(within(newer).getByText("FSA-2026-001")).toBeInTheDocument();
    await waitFor(() => expect(within(newer).queryByRole("link", { name: /^Release notes/ })).toBeNull());
  });
});
