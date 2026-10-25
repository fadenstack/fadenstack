/** Notices above every admin page (AdminNotices.tsx): worded from their codes, errors not to be hidden. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import * as admin from "~/api/admin";

import { AdminNotices } from "./AdminNotices";

let list: MockInstance<typeof admin.adminNotices.list>;

beforeEach(() => {
  sessionStorage.clear();
  list = vi.spyOn(admin.adminNotices, "list");
});

describe("admin notices", () => {
  it("words the licence's countdown, with its day and date", async () => {
    list.mockResolvedValue([
      { code: "licence_expiring", severity: "warning", params: { days: 12, date: "2026-11-05" } },
      { code: "licence_seats_over", severity: "warning", params: { used: 12, seats: 10 } },
    ]);
    render(<AdminNotices isSuperuser />);
    expect(await screen.findByTestId("admin-notice-licence_expiring")).toHaveTextContent(
      "The Enterprise licence expires in 12 days, on Nov 5, 2026.",
    );
    expect(screen.getByTestId("admin-notice-licence_seats_over")).toHaveTextContent("12 active accounts");
  });

  it("counts one day as one", async () => {
    list.mockResolvedValue([{ code: "licence_grace", severity: "error", params: { days: 1, date: "2026-11-20" } }]);
    render(<AdminNotices isSuperuser />);
    expect(await screen.findByTestId("admin-notice-licence_grace")).toHaveTextContent("stop in 1 day,");
  });

  it("hides a warning until the browser is closed, never an error", async () => {
    list.mockResolvedValue([
      { code: "licence_expired", severity: "error", params: { date: "2026-10-23" } },
      { code: "licence_missing", severity: "warning", params: {} },
    ]);
    render(<AdminNotices isSuperuser />);
    const warning = await screen.findByTestId("admin-notice-licence_missing");
    const error = screen.getByTestId("admin-notice-licence_expired");
    expect(error.querySelector("button")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hide until the browser is closed" }));
    await waitFor(() => expect(warning).not.toBeInTheDocument());
    expect(screen.getByTestId("admin-notice-licence_expired")).toBeInTheDocument();
    expect(sessionStorage.getItem("faden.notices.hidden")).toContain("licence_missing");
  });

  it("shows a hidden countdown again once it moves on", async () => {
    sessionStorage.setItem("faden.notices.hidden", JSON.stringify(['licence_expiring:{"days":12,"date":"2026-11-05"}']));
    list.mockResolvedValue([{ code: "licence_expiring", severity: "warning", params: { days: 11, date: "2026-11-05" } }]);
    render(<AdminNotices isSuperuser />);
    expect(await screen.findByTestId("admin-notice-licence_expiring")).toHaveTextContent("11 days");
  });

  it("says something for a code it has no words for", async () => {
    list.mockResolvedValue([{ code: "brand_new", severity: "info", params: {} }]);
    render(<AdminNotices isSuperuser />);
    expect(await screen.findByTestId("admin-notice-brand_new")).toHaveTextContent("A notice for administrators: brand_new");
  });

  it("asks nothing for someone who is not a superuser, and shows nothing when the request fails", async () => {
    const { container } = render(<AdminNotices isSuperuser={false} />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(list).not.toHaveBeenCalled();

    list.mockRejectedValue(new Error("403"));
    const other = render(<AdminNotices isSuperuser />);
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(other.container).toBeEmptyDOMElement();
  });
});
