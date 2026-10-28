/**
 * The administrators' Usage page: every figure over at least k people, with
 * the rule on the page (M04); the small-instance banner (S12); groups by
 * name, never ranked (M11); and per-person figures only for a usage auditor,
 * after a reason, with the refusals in words (M02, M06, M07).
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import type { AdminUser, Group } from "~/api/admin";
import { ApiError } from "~/api/http";
import * as api from "~/api/usage";
import { getAppTheme } from "~/theme";
import { renderPage } from "~/test/renderPage";
import { adminUsage, hiddenMeasures, measures } from "~/test/usageFixtures";

import UsagePage from "./UsagePage";

vi.mock("~/api/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/usage")>();
  return { ...actual, usageApi: { ...actual.usageApi, admin: vi.fn(), people: vi.fn() } };
});

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return {
    ...actual,
    adminUsers: { ...actual.adminUsers, list: vi.fn() },
    adminGroups: { ...actual.adminGroups, list: vi.fn() },
  };
});

// The chart is MUI's; what goes into it is the page's (a gap is null, never 0).
vi.mock("~/components/dashboard/TimeSeriesChart", () => ({
  default: ({ ariaLabel, series }: { ariaLabel: string; series: { id: string; data: (number | null)[] }[] }) => (
    <div role="img" aria-label={ariaLabel} data-series={JSON.stringify(series.map((s) => s.data))} />
  ),
}));

const usage = vi.mocked(api.usageApi);
const users = vi.mocked(admin.adminUsers);
const groups = vi.mocked(admin.adminGroups);

function view() {
  return renderPage(
    <ThemeProvider theme={getAppTheme("light")}>
      <UsagePage />
    </ThemeProvider>,
    { path: "/admin/usage" },
  );
}

/** The group names in the order the table shows them. */
function groupOrder(): string[] {
  const table = screen.getByRole("table", { name: "By usage group" });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent ?? "");
}

beforeEach(() => {
  usage.admin.mockReset();
  usage.people.mockReset();
  users.list.mockResolvedValue([{ id: "u-1", email: "pat@example.com" } as AdminUser]);
  groups.list.mockResolvedValue([{ id: "g-sales", name: "Sales" } as Group]);
});

describe("Usage (administrators)", () => {
  it("shows the totals and the groups, with the k rule written out", async () => {
    usage.admin.mockResolvedValue(adminUsage());
    const { container } = view();

    expect(await screen.findByRole("heading", { name: "Usage" })).toBeInTheDocument();
    expect(container.querySelector("[data-usage-k-rule]")?.textContent).toMatch(
      /Every figure here covers at least 5 people\. Groups with fewer than 5 people are merged into one row/,
    );
    const totals = screen.getByRole("region", { name: "Totals" });
    expect(within(totals).getByText("21")).toBeInTheDocument();

    const table = screen.getByRole("table", { name: "By usage group" });
    expect(within(table).getByText("Smaller teams and others")).toBeInTheDocument();
    expect(within(table).getByText("5 or more")).toBeInTheDocument();
    expect(container.querySelector("[data-usage-small-instance]")).toBeNull();
  });

  it("lists groups by name, the merged row last, and offers no sorting by usage (M11)", async () => {
    // The server's order puts the heaviest user first; the page does not follow it.
    usage.admin.mockResolvedValue(
      adminUsage({
        group_by: "agent",
        groups: [
          { key: "a-z", label: "Zeta", other: false, people: 12, suppressed: false, ...measures({ requests: 9000 }) },
          { key: null, label: null, other: true, people: null, suppressed: true, ...measures({ requests: 40 }) },
          { key: null, label: null, other: false, people: 8, suppressed: false, ...measures({ requests: 500 }) },
          { key: "a-a", label: "Alpha", other: false, people: 6, suppressed: false, ...measures({ requests: 10 }) },
        ],
      }),
    );
    view();
    const table = await screen.findByRole("table", { name: "By agent" });

    const order = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[0].textContent ?? "");
    expect(order).toEqual(["Alpha", "Zeta", "No agent", expect.stringContaining("Fewer than 5 people")]);
    expect(within(table).queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText(/Listed by name; nothing here is ranked./)).toBeInTheDocument();
  });

  it("names the rows without a key by what they stand for", async () => {
    usage.admin.mockResolvedValue(
      adminUsage({
        group_by: "model",
        groups: [{ key: null, label: null, other: false, people: 9, suppressed: false, ...measures() }],
      }),
    );
    view();

    expect(await screen.findByText("Unknown model")).toBeInTheDocument();
  });

  it("says when the instance is too small for totals to stay anonymous (S12)", async () => {
    usage.admin.mockResolvedValue(adminUsage({ small_instance: true, totals: { ...measures(), people: null } }));
    const { container } = view();

    await waitFor(() => expect(container.querySelector("[data-usage-small-instance]")).not.toBeNull());
    const banner = container.querySelector("[data-usage-small-instance]") as HTMLElement;
    expect(within(banner).getByText("A small instance")).toBeInTheDocument();
    expect(banner.textContent).toMatch(/Fewer than 5 people used this instance/);
    // The people count below k is not shown as a number.
    expect(within(screen.getByRole("region", { name: "Totals" })).getByText("Fewer than 5")).toBeInTheDocument();
  });

  it("leaves hours with fewer than k people out of the chart, as gaps", async () => {
    usage.admin.mockResolvedValue(
      adminUsage({
        bucket: "hour",
        series: [
          { period: "2026-10-28T08:00:00Z", suppressed: false, ...measures({ input_tokens: 100, output_tokens: 10 }) },
          { period: "2026-10-28T09:00:00Z", suppressed: true, ...hiddenMeasures() },
        ],
      }),
    );
    view();

    const chart = await screen.findByRole("img", { name: "Tokens over time" });
    expect(JSON.parse(chart.getAttribute("data-series")!)).toEqual([
      [100, null],
      [10, null],
    ]);
    expect(screen.getByText("Hours with fewer than 5 people are left out.")).toBeInTheDocument();
  });

  it("offers no per-person view without the usage auditor right", async () => {
    usage.admin.mockResolvedValue(adminUsage({ person_reporting: true, can_view_people: false }));
    view();

    expect(await screen.findByText(/Only people with the usage auditor role see the figures of single people/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View figures per person" })).toBeNull();
  });

  it("says that nobody sees single people while reporting is off", async () => {
    usage.admin.mockResolvedValue(adminUsage());
    view();

    expect(await screen.findByText(/Person-level reporting is off: nobody sees the figures of a single person/)).toBeInTheDocument();
  });
});

describe("teams as a tree", () => {
  const tree = () =>
    adminUsage({
      groups: [
        // As the server sends them: a team's figures include its sub-teams.
        { key: "g-sales", label: "Sales", other: false, people: 14, suppressed: false, parent_key: null, path: ["g-sales"], ...measures({ requests: 900 }) },
        { key: "g-emea", label: "EMEA", other: false, people: 8, suppressed: false, parent_key: "g-sales", path: ["g-sales", "g-emea"], ...measures({ requests: 600 }) },
        { key: null, label: null, other: true, people: null, suppressed: true, parent_key: "g-sales", path: ["g-sales"], ...measures({ requests: 300 }) },
        { key: "g-eng", label: "Engineering", other: false, people: 6, suppressed: false, parent_key: null, path: ["g-eng"], ...measures({ requests: 50 }) },
        { key: null, label: null, other: false, people: 5, suppressed: false, parent_key: null, path: [], ...measures({ requests: 20 }) },
      ],
    });

  it("shows the top teams and Unassigned, and opens a team to its sub-teams and its smaller teams", async () => {
    usage.admin.mockResolvedValue(tree());
    view();
    await screen.findByRole("table", { name: "By usage group" });

    expect(groupOrder()).toEqual(["Engineering", "Sales", expect.stringContaining("Unassigned")]);

    await userEvent.click(screen.getByRole("button", { name: "Show the teams below Sales" }));
    expect(groupOrder()).toEqual([
      "Engineering",
      "Sales",
      "EMEA",
      expect.stringContaining("Smaller teams"),
      expect.stringContaining("Unassigned"),
    ]);
    expect(document.querySelector("[data-usage-node='t:g-sales/g-emea']")?.getAttribute("data-usage-depth")).toBe("2");

    await userEvent.click(screen.getByRole("button", { name: "Hide the teams below Sales" }));
    expect(groupOrder()).toHaveLength(3);
  });

  it("asks for today's structure when switched, as recorded by default", async () => {
    usage.admin.mockResolvedValue(tree());
    view();
    await screen.findByRole("table", { name: "By usage group" });
    expect(usage.admin).toHaveBeenLastCalledWith(expect.objectContaining({ group_by: "team", structure: "recorded" }));

    await userEvent.click(screen.getByRole("button", { name: "Today's structure" }));
    await waitFor(() => expect(usage.admin).toHaveBeenLastCalledWith(expect.objectContaining({ structure: "current" })));
    expect(screen.getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help/administer/usage/#teams-on-the-usage-page");

    // Not a choice for agents, models or providers.
    await userEvent.click(screen.getByRole("button", { name: "Agent" }));
    await waitFor(() => expect(usage.admin).toHaveBeenLastCalledWith(expect.not.objectContaining({ structure: expect.anything() })));
    expect(screen.queryByRole("button", { name: "Today's structure" })).toBeNull();
  });

  it("hides the totals of a range with fewer than k people", async () => {
    usage.admin.mockResolvedValue(
      adminUsage({ totals: { ...hiddenMeasures(), people: null }, groups: [], small_instance: true }),
    );
    view();

    const totals = await screen.findByRole("region", { name: "Totals" });
    expect(await within(totals).findByText("Fewer than 5")).toBeInTheDocument();
    expect(within(totals).queryByText(/\d{2,}/)).toBeNull();
  });

  it("lets a usage auditor show them anyway, after a reason", async () => {
    usage.admin.mockResolvedValue(
      adminUsage({
        totals: { ...hiddenMeasures(), people: null },
        small_instance: true,
        person_reporting: true,
        can_view_people: true,
      }),
    );
    const { container } = view();
    await userEvent.click(await screen.findByRole("button", { name: "Show anyway" }));
    const dialog = await screen.findByRole("dialog", { name: "Show figures about fewer than 5 people" });
    const show = within(dialog).getByRole("button", { name: "Show figures" });
    expect(show).toBeDisabled();

    usage.admin.mockResolvedValue(adminUsage({ small_instance: true, unhidden: true, can_view_people: true }));
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Cost dispute of the pilot team");
    await userEvent.click(show);

    await waitFor(() =>
      expect(usage.admin).toHaveBeenLastCalledWith(expect.objectContaining({ reason: "Cost dispute of the pilot team" })),
    );
    await waitFor(() => expect(container.querySelector("[data-usage-unhidden]")).not.toBeNull());
  });

  it("words a refused reason and stops sending it", async () => {
    usage.admin.mockResolvedValue(
      adminUsage({ totals: { ...hiddenMeasures(), people: null }, small_instance: true, can_view_people: true }),
    );
    view();
    await userEvent.click(await screen.findByRole("button", { name: "Show anyway" }));
    const dialog = await screen.findByRole("dialog");

    usage.admin.mockRejectedValueOnce(new ApiError("forbidden", 403, "not_usage_auditor"));
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Cost dispute of the pilot team");
    await userEvent.click(within(dialog).getByRole("button", { name: "Show figures" }));

    expect(await screen.findByText(/need the usage auditor role/)).toBeInTheDocument();
    await waitFor(() => expect(usage.admin).toHaveBeenLastCalledWith(expect.not.objectContaining({ reason: expect.anything() })));
  });
});

describe("per-person figures for a usage auditor", () => {
  beforeEach(() => {
    usage.admin.mockResolvedValue(adminUsage({ person_reporting: true, can_view_people: true }));
  });

  async function openDialog() {
    view();
    await userEvent.click(await screen.findByRole("button", { name: "View figures per person" }));
    return screen.findByRole("dialog", { name: "View figures per person" });
  }

  async function pickTeam(dialog: HTMLElement) {
    await userEvent.click(within(dialog).getByRole("button", { name: "One usage group" }));
    await userEvent.click(within(dialog).getByRole("combobox", { name: "Usage group" }));
    await userEvent.click(await screen.findByRole("option", { name: "Sales" }));
  }

  it("asks for a reason first and fetches nothing before it", async () => {
    const dialog = await openDialog();
    await pickTeam(dialog);
    const show = within(dialog).getByRole("button", { name: "Show figures" });
    expect(show).toBeDisabled();

    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "budget");
    expect(show).toBeDisabled();
    expect(within(dialog).getByText("Give a reason of 10 to 500 characters.")).toBeInTheDocument();
    expect(usage.people).not.toHaveBeenCalled();

    usage.people.mockResolvedValue({
      granularity: "day",
      rows: [
        { person: { id: "u-1", name: "Pat Example", email: "pat@example.com" }, period: "2026-10-27", ...measures() },
        { person: { id: "u-2", name: null, email: "sam@example.com" }, period: "2026-10-27", ...measures() },
        { person: { id: "u-3", name: "kim@example.com", email: "kim@example.com" }, period: "2026-10-27", ...measures() },
      ],
    });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), " dispute in Sales");
    expect(show).toBeEnabled();
    await userEvent.click(show);

    await waitFor(() =>
      expect(usage.people).toHaveBeenCalledWith({
        from: "2026-09-29",
        to: "2026-10-28",
        team: "g-sales",
        reason: "budget dispute in Sales",
      }),
    );
    const table = await screen.findByRole("table", { name: "Figures per person" });
    expect(within(table).getByText("Pat Example")).toBeInTheDocument();
    expect(within(table).getByText("sam@example.com")).toBeInTheDocument();
    expect(within(table).getAllByText("kim@example.com")).toHaveLength(1); // a name that is the address, once
    expect(within(table).queryAllByRole("button")).toHaveLength(0);

    // One person's data, downloaded under the same reason.
    const json = screen.getByRole("link", { name: "Download as JSON" });
    expect(json).toHaveAttribute("href", "/api/admin/usage/people/u-1/export?format=json&reason=budget+dispute+in+Sales");
    await userEvent.click(screen.getByRole("combobox", { name: "Person" }));
    await userEvent.click(await screen.findByRole("option", { name: "sam@example.com" }));
    expect(screen.getByRole("link", { name: "Download as CSV" })).toHaveAttribute(
      "href",
      "/api/admin/usage/people/u-2/export?format=csv&reason=budget+dispute+in+Sales",
    );
  });

  it.each([
    [new ApiError("forbidden", 403, "not_usage_auditor"), /need the usage auditor role\. Being an administrator is not enough/],
    [new ApiError("forbidden", 403, "person_reporting_off"), /Person-level reporting is off, so the figures of single people are not available/],
    [new ApiError("unprocessable", 422, "reason_required"), /Give a reason of 10 to 500 characters/],
  ])("words the refusal %#", async (refusal, words) => {
    usage.people.mockRejectedValue(refusal);
    const dialog = await openDialog();
    await pickTeam(dialog);
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Access request by the person");
    await userEvent.click(within(dialog).getByRole("button", { name: "Show figures" }));

    await waitFor(() => expect(dialog.querySelector("[data-usage-people-error]")).not.toBeNull());
    expect(dialog.querySelector("[data-usage-people-error]")!.textContent).toMatch(words);
    expect(document.querySelector("[data-usage-person-export]")).toBeNull(); // nothing to download without a view
  });
});
