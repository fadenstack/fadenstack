/**
 * Needs attention: a problem someone has seen can be muted, and shown again.
 *
 * Old warnings stayed at the top of the dashboard for as long as their cause
 * did -- a stopped deployment's failure, a node long gone -- with no way to
 * say "seen, I am on it".
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dashboardFleet, type AttentionResponse } from "~/api/dashboard";
import { clearDashboardQueries } from "~/lib/dashboardQuery";
import { getAppTheme } from "~/theme";
import AttentionWidget from "./AttentionWidget";

const offline = {
  id: "node_offline:x",
  code: "node_offline",
  severity: "critical" as const,
  category: "infrastructure",
  params: { node: "spark-1" },
  since: null,
  link: "/admin/nodes/x",
};

function response(muted: boolean): AttentionResponse {
  return {
    generated_at: "2026-10-03T12:00:00Z",
    items: muted ? [] : [offline],
    counts: { critical: muted ? 0 : 1, warning: 0, info: 0 },
    rule_errors: [],
    muted: muted
      ? [{ ...offline, acknowledged_at: "2026-10-03T11:58:00Z", acknowledged_by: "admin@example.com" }]
      : [],
  };
}

function view() {
  return render(
    <ThemeProvider theme={getAppTheme("light")}>
      <MemoryRouter>
        <AttentionWidget size="M" layout={{} as never} onLayoutChange={() => {}} refreshToken={0} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("AttentionWidget muting", () => {
  let muted = false;

  beforeEach(() => {
    muted = false;
    vi.spyOn(dashboardFleet, "attention").mockImplementation(() => Promise.resolve(response(muted)));
    vi.spyOn(dashboardFleet, "acknowledge").mockImplementation(() => {
      muted = true;
      return Promise.resolve({ item_id: offline.id, muted: true });
    });
    vi.spyOn(dashboardFleet, "unacknowledge").mockImplementation(() => {
      muted = false;
      return Promise.resolve({ item_id: offline.id, muted: false });
    });
  });

  afterEach(() => {
    clearDashboardQueries();
    vi.restoreAllMocks();
  });

  it("mutes an item, lists it apart with who muted it, and shows it again", async () => {
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Mute" }));

    expect(dashboardFleet.acknowledge).toHaveBeenCalledWith(offline);
    const toggle = await screen.findByRole("button", { name: "1 muted" });
    expect(screen.getByText("All clear")).toBeTruthy();

    fireEvent.click(toggle);
    const list = screen.getByTestId("attention-muted");
    expect(within(list).getByText(/Muted by admin@example.com/)).toBeTruthy();

    fireEvent.click(within(list).getByRole("button", { name: "Show again" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "1 muted" })).toBeNull());
    expect(dashboardFleet.unacknowledge).toHaveBeenCalledWith(offline.id);
    expect(screen.getByRole("button", { name: "Mute" })).toBeTruthy();
  });

  it("keeps the item's link apart from the mute button", async () => {
    view();
    const mute = await screen.findByRole("button", { name: "Mute" });
    expect(mute.closest("a")).toBeNull();
    expect(screen.getByRole("link").getAttribute("href")).toBe("/admin/nodes/x");
  });
});
