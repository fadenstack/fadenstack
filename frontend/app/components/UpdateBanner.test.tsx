/**
 * The update banner on admin pages: shown for a critical or security release
 * and for a release past its support; a security one can be hidden until the
 * next release, the others cannot; never on the Updates page itself.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { updatesApi, type UpdateNotice } from "~/api/updates";
import { clearDashboardQueries } from "~/lib/dashboardQuery";
import { BANNER_DISMISSED_KEY, updateBannerState } from "~/lib/updateNotice";
import { UpdateBanner } from "./UpdateBanner";

function notice(overrides: Partial<UpdateNotice> = {}): UpdateNotice {
  return {
    current: "0.4.1",
    latest: "0.4.2",
    severity: "security",
    update_available: true,
    unsupported: false,
    supported_until: null,
    ...overrides,
  };
}

describe("updateBannerState", () => {
  it("says nothing for a regular release, none, or no notice", () => {
    expect(updateBannerState(notice({ severity: "normal" }), null)).toBeNull();
    expect(updateBannerState(notice({ severity: "none", update_available: false, latest: "0.4.1" }), null)).toBeNull();
    expect(updateBannerState(undefined, null)).toBeNull();
  });

  it("lets a security update be hidden, until the next release", () => {
    expect(updateBannerState(notice(), null)).toEqual({ kind: "security", dismissible: true, unsupported: false });
    expect(updateBannerState(notice(), "0.4.2")).toBeNull();
    expect(updateBannerState(notice({ latest: "0.4.3" }), "0.4.2")).toMatchObject({ kind: "security" });
  });

  it("never hides a critical update or a release past its support", () => {
    expect(updateBannerState(notice({ severity: "critical" }), "0.4.2")).toEqual({
      kind: "critical",
      dismissible: false,
      unsupported: false,
    });
    const old = notice({ severity: "normal", unsupported: true, supported_until: "2026-09-30" });
    expect(updateBannerState(old, "0.4.2")).toEqual({ kind: "unsupported", dismissible: false, unsupported: true });
    expect(updateBannerState(notice({ unsupported: true }), "0.4.2")).toEqual({
      kind: "security",
      dismissible: false,
      unsupported: true,
    });
  });
});

describe("UpdateBanner", () => {
  let current: UpdateNotice;

  beforeEach(() => {
    current = notice();
    window.localStorage.removeItem(BANNER_DISMISSED_KEY);
    vi.spyOn(updatesApi, "notice").mockImplementation(() => Promise.resolve(current));
  });

  afterEach(() => {
    clearDashboardQueries();
    window.localStorage.removeItem(BANNER_DISMISSED_KEY);
    vi.restoreAllMocks();
  });

  function view(pathname = "/admin/dashboard", isSuperuser = true) {
    return render(
      <MemoryRouter>
        <UpdateBanner isSuperuser={isSuperuser} pathname={pathname} />
      </MemoryRouter>,
    );
  }

  it("says what the security release fixes, links to Updates, and stays hidden once dismissed", async () => {
    const { unmount } = view();
    const banner = await screen.findByTestId("update-banner");
    expect(banner).toHaveTextContent("A newer release fixes security problems: Fadenstack 0.4.2 is out, this server runs 0.4.1.");
    expect(screen.getByRole("link", { name: "See updates" })).toHaveAttribute("href", "/admin/updates");

    fireEvent.click(screen.getByRole("button", { name: "Hide until the next release" }));
    await waitFor(() => expect(screen.queryByTestId("update-banner")).toBeNull());
    expect(window.localStorage.getItem(BANNER_DISMISSED_KEY)).toBe("0.4.2");

    unmount();
    view();
    // The notice is there (shared, cached), the banner is not.
    await waitFor(() => expect(updatesApi.notice).toHaveBeenCalled());
    expect(screen.queryByTestId("update-banner")).toBeNull();
  });

  it("cannot be dismissed for a critical release", async () => {
    current = notice({ severity: "critical" });
    view();
    expect(await screen.findByTestId("update-banner")).toHaveTextContent("A newer release fixes a critical problem: Fadenstack 0.4.2 is out");
    expect(screen.queryByRole("button", { name: "Hide until the next release" })).toBeNull();
  });

  it("is not shown on the Updates page, nor to anyone but a superuser", async () => {
    view("/admin/updates");
    await waitFor(() => expect(updatesApi.notice).toHaveBeenCalled());
    expect(screen.queryByTestId("update-banner")).toBeNull();
    clearDashboardQueries();
    vi.mocked(updatesApi.notice).mockClear();

    view("/admin/dashboard", false);
    expect(screen.queryByTestId("update-banner")).toBeNull();
    expect(updatesApi.notice).not.toHaveBeenCalled();
  });
});
