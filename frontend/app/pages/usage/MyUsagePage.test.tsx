/**
 * Everyone's own usage page (M03): the figures, the limits, the downloads,
 * and the notice of who can see the figures -- plain when person-level
 * reporting is on (M14).
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "~/api/usage";
import { getAppTheme } from "~/theme";
import { renderPage } from "~/test/renderPage";
import { measures, myUsage } from "~/test/usageFixtures";
import { todayIn } from "~/components/usage/usageFormat";

import MyUsagePage from "./MyUsagePage";

function view() {
  return renderPage(
    <ThemeProvider theme={getAppTheme("light")}>
      <MyUsagePage />
    </ThemeProvider>,
    { path: "/usage" },
  );
}

vi.mock("~/api/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/usage")>();
  return { ...actual, usageApi: { ...actual.usageApi, me: vi.fn() } };
});

// The person's own chat projects, which name the project rows.
vi.mock("~/api/chatClient", () => ({
  chatApi: { listProjects: vi.fn().mockResolvedValue([{ id: "p-1", name: "Quarterly report" }]) },
}));

// The chart itself is MUI's; the page decides what goes into it.
vi.mock("~/components/dashboard/TimeSeriesChart", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} />,
}));

const me = vi.mocked(api.usageApi.me);

beforeEach(() => {
  me.mockReset();
});

describe("My usage", () => {
  it("shows the person's totals, breakdowns, limits and downloads", async () => {
    me.mockResolvedValue(myUsage());
    view();

    expect(await screen.findByRole("heading", { name: "My usage" })).toBeInTheDocument();
    const totals = await screen.findByRole("region", { name: "Totals" });
    expect(within(totals).getByText("1,234")).toBeInTheDocument();
    expect(within(totals).getByText("€1.25")).toBeInTheDocument();
    expect(within(totals).getByText("12,000 of them cached")).toBeInTheDocument();

    const byModel = screen.getByRole("region", { name: "By model" });
    expect(within(byModel).getByText("qwen3.8-27b-fp8")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "By agent" })).getByText("Without an agent")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "By project" })).getByText("No usage in this period.")).toBeInTheDocument();

    const limits = screen.getByRole("region", { name: "Limits" });
    expect(within(limits).getByText("Tokens per day")).toBeInTheDocument();
    expect(within(limits).getByText("120 of 1,000")).toBeInTheDocument();
    expect(within(limits).getByText(/apply to the whole instance/)).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Download as JSON" })).toHaveAttribute("href", "/api/usage/me/export?format=json");
    expect(screen.getByRole("link", { name: "Download as CSV" })).toHaveAttribute("href", "/api/usage/me/export?format=csv");
    expect(screen.getByRole("img", { name: "Tokens over time" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help/administer/usage/#your-own-usage");
  });

  it("says that nobody sees the figures alone while person-level reporting is off", async () => {
    me.mockResolvedValue(myUsage());
    const { container } = view();

    expect(await screen.findByText(/Only you see your own figures/)).toBeInTheDocument();
    expect(container.querySelector("[data-usage-privacy='on']")).toBeNull();
  });

  it("says plainly who can see the figures, why, and for how long when it is on (M14)", async () => {
    me.mockResolvedValue(
      myUsage({ privacy: { person_reporting: true, granularity: "day", retention_days: 90, views_of_my_data: 2 } }),
    );
    view();

    const notice = (await screen.findByText("Person-level reporting is on")).closest("[data-usage-privacy='on']") as HTMLElement;
    expect(notice).not.toBeNull();
    expect(within(notice).getByText("People with the usage auditor role can see your figures per day.")).toBeInTheDocument();
    expect(within(notice).getByText(/cost and capacity control, not to evaluate your work/)).toBeInTheDocument();
    expect(within(notice).getByText("Your per-person figures are kept for 90 days, then deleted.")).toBeInTheDocument();
    expect(within(notice).getByText(/viewed 2 times/)).toBeInTheDocument();
  });

  it("draws an extension's blocks as a heading and their fields", async () => {
    me.mockResolvedValue(myUsage({ sections: [{ kind: "budgets", data: { monthly_tokens: 1000000, spent: 250000 } }] }));
    view();

    const block = await screen.findByRole("region", { name: "Budgets" });
    expect(within(block).getByText("monthly_tokens")).toBeInTheDocument();
    expect(within(block).getByText("1,000,000")).toBeInTheDocument();
    expect(within(block).getByText("250,000")).toBeInTheDocument();
  });

  it("shows no cost where no price applies, rather than a zero", async () => {
    me.mockResolvedValue(myUsage({ totals: measures({ cost: {} }) }));
    view();

    const totals = await screen.findByRole("region", { name: "Totals" });
    expect(within(totals).queryByText(/€|\$/)).toBeNull();
  });

  it("names chat projects from the person's own projects", async () => {
    me.mockResolvedValue(
      myUsage({
        by_project: [
          { key: "p-1", label: null, ...measures() },
          { key: "p-gone", label: null, ...measures() },
          { key: null, label: null, ...measures() },
        ],
      }),
    );
    view();

    const projects = await screen.findByRole("region", { name: "By project" });
    expect(await within(projects).findByText("Quarterly report")).toBeInTheDocument();
    expect(within(projects).getByText("A chat project")).toBeInTheDocument();
    expect(within(projects).getByText("Without a project")).toBeInTheDocument();
  });

  it("links to the organisation's privacy notice when one is set", async () => {
    me.mockResolvedValue(
      myUsage({
        privacy: {
          person_reporting: false,
          granularity: "day",
          retention_days: 90,
          views_of_my_data: 0,
          notice_url: "https://intranet.example.com/privacy",
        },
      }),
    );
    view();

    expect(await screen.findByRole("link", { name: "Read the privacy notice" })).toHaveAttribute(
      "href",
      "https://intranet.example.com/privacy",
    );
  });

  it("shows only the limit where how much is used is not known", async () => {
    me.mockResolvedValue(
      myUsage({
        caps: [
          { kind: "rpm", limit: 600, used: null, resets_at: "2026-10-28T10:01:00Z" },
          { kind: "tokens_per_day", limit: 1000000, used: null, resets_at: "2026-10-28T22:00:00Z" },
        ],
      }),
    );
    const { container } = view();

    const limits = await screen.findByRole("region", { name: "Limits" });
    expect(await within(limits).findByText("Limit: 600")).toBeInTheDocument();
    expect(within(limits).getByText(/Counted per minute/)).toBeInTheDocument();
    expect(within(limits).getByText(/once enough other people have used the instance today/)).toBeInTheDocument();
    expect(container.querySelector("[role='progressbar']")).toBeNull();
  });

  it("counts days in the usage time zone once the server names it", async () => {
    me.mockResolvedValue(myUsage({ time_zone: "Pacific/Kiritimati" }));
    view();
    await screen.findByRole("region", { name: "Totals" });

    await waitFor(() => expect(me).toHaveBeenLastCalledWith(expect.objectContaining({ to: todayIn("Pacific/Kiritimati") })));
  });

  it("asks again for another period and per month", async () => {
    me.mockResolvedValue(myUsage());
    view();
    await screen.findByRole("region", { name: "Totals" });

    await userEvent.click(screen.getByRole("button", { name: "Month" }));

    await waitFor(() => expect(me).toHaveBeenLastCalledWith(expect.objectContaining({ bucket: "month" })));
    const { from, to } = me.mock.calls.at(-1)![0]!;
    expect(from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(to).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
