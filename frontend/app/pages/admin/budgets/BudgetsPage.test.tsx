/**
 * The Budgets page: each budget with what is used of it, the forecast, what a shadow budget would have
 * refused and the warnings (teams below adding up to more; a currency some models are not priced in); a
 * person's budget with its limit only (M17); deleting with a confirmation; and nothing at all while the
 * server does not offer budgets.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { MemoryRouter, Outlet, Route, Routes } from "react-router";
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hasFeature } from "~/api/about";
import * as admin from "~/api/admin";
import type { AdminUser, Group } from "~/api/admin";
import * as agents from "~/api/agents";
import * as api from "~/api/budgets";
import * as observability from "~/api/observability";
import type { PricingEntry } from "~/api/observability";
import * as usage from "~/api/usage";
import { getAppTheme } from "~/theme";
import { budget, budgetSettings } from "~/test/budgetFixtures";
import { usageSettings } from "~/test/usageFixtures";

import BudgetsPage from "./BudgetsPage";

vi.mock("~/api/about", () => ({ hasFeature: vi.fn() }));

vi.mock("~/api/budgets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/budgets")>();
  return {
    ...actual,
    budgetsApi: {
      ...actual.budgetsApi,
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      settings: vi.fn(),
      updateSettings: vi.fn(),
      chargeback: vi.fn(),
    },
  };
});

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return {
    ...actual,
    adminUsers: { ...actual.adminUsers, list: vi.fn() },
    adminGroups: { ...actual.adminGroups, list: vi.fn() },
  };
});

vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return { ...actual, agentsApi: { ...actual.agentsApi, list: vi.fn() } };
});

vi.mock("~/api/observability", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/observability")>();
  return { ...actual, observability: { ...actual.observability, listPricing: vi.fn() } };
});

vi.mock("~/api/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/usage")>();
  return { ...actual, usageApi: { ...actual.usageApi, settings: vi.fn() } };
});

const budgets = vi.mocked(api.budgetsApi);

function price(currency: string): PricingEntry {
  return { id: currency, provider: "p", model: "m", currency, active: true } as PricingEntry;
}

function view(path = "/admin/budgets") {
  return render(
    <ThemeProvider theme={getAppTheme("light")}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Outlet context={{ rootModeActive: false }} />}>
            <Route path="/admin/budgets" element={<BudgetsPage />} />
            <Route path="/admin/usage" element={<p>The usage page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  vi.mocked(hasFeature).mockReset();
  vi.mocked(hasFeature).mockImplementation(async (feature) => feature === api.BUDGETS);
  budgets.list.mockReset();
  budgets.remove.mockReset();
  budgets.chargeback.mockReset();
  budgets.settings.mockReset();
  budgets.list.mockResolvedValue([budget()]);
  budgets.settings.mockResolvedValue(budgetSettings());
  vi.mocked(admin.adminUsers.list).mockResolvedValue([{ id: "u-owner", email: "alex@example.com" } as AdminUser]);
  vi.mocked(admin.adminGroups.list).mockResolvedValue([{ id: "g-sales", name: "Sales" } as Group]);
  vi.mocked(agents.agentsApi.list).mockResolvedValue([]);
  vi.mocked(observability.observability.listPricing).mockResolvedValue([price("EUR")]);
  vi.mocked(usage.usageApi.settings).mockResolvedValue(usageSettings());
});

describe("Budgets", () => {
  it("lists each budget with whom it applies to, the period, the mode and what is used", async () => {
    const { container } = view();

    expect(await screen.findByRole("heading", { name: "Budgets", level: 1 })).toBeInTheDocument();
    const row = await screen.findByRole("listitem", { name: "Sales per month" });
    expect(within(row).getByText("Team · Sales")).toBeInTheDocument();
    expect(within(row).getByText("Per month")).toBeInTheDocument();
    expect(within(row).getByText("Hard")).toBeInTheDocument();
    expect(within(row).getByText("Limit 1,000,000 tokens")).toBeInTheDocument();
    expect(within(row).getByText("This period: Oct 1, 2026 to Oct 31, 2026")).toBeInTheDocument();
    expect(within(row).getByText("250,000 of 1,000,000 tokens")).toBeInTheDocument();
    expect(within(row).getByText("25 %")).toBeInTheDocument();
    expect(within(row).getByRole("progressbar", { name: "Used of Sales per month" })).toHaveAttribute("aria-valuenow", "25");
    expect(within(row).getByText("Alerts at 80 %, 100 %")).toBeInTheDocument();
    // The rule every budget follows is on the page, with its Help.
    expect(container.querySelector("[data-budget-rule]")?.textContent).toMatch(/needs room in every budget that applies/);
    expect(screen.getAllByRole("link", { name: "Help" })[0]).toHaveAttribute("href", "/help/administer/budgets/");
  });

  it("shows the forecast, what a shadow budget would have refused, and the owner", async () => {
    budgets.list.mockResolvedValue([
      budget({ forecast_end: "2026-10-24", percent: 85, spent: 850000, owner_id: "u-owner" }),
      budget({ id: "b-try", name: "Trial", mode: "shadow", would_refuse: 12, scope: "tenant", target_id: null, target_label: null }),
    ]);
    view();

    const sales = await screen.findByRole("listitem", { name: "Sales per month" });
    expect(within(sales).getByText("At this rate it runs out on Oct 24, 2026, before the period ends.")).toBeInTheDocument();
    expect(within(sales).getByText(/also to alex@example\.com/)).toBeInTheDocument();
    const trial = screen.getByRole("listitem", { name: "Trial" });
    expect(within(trial).getByText("The whole organisation")).toBeInTheDocument();
    expect(within(trial).getByText("Shadow")).toBeInTheDocument();
    expect(within(trial).getByText("Would have refused 12 requests this period.")).toBeInTheDocument();
  });

  it("lists budgets by name, never by how much is used", async () => {
    budgets.list.mockResolvedValue([
      budget({ id: "b-z", name: "Zeta", percent: 99 }),
      budget({ id: "b-a", name: "Alpha", percent: 1 }),
    ]);
    view();

    await screen.findByRole("listitem", { name: "Alpha" });
    expect(screen.getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual(["Alpha", "Zeta"]);
  });

  it("warns when the teams below add up to more, and about currencies that do not count", async () => {
    vi.mocked(observability.observability.listPricing).mockResolvedValue([price("EUR"), price("USD")]);
    budgets.list.mockResolvedValue([
      budget({ children_exceed: true }),
      budget({ id: "b-eur", name: "Euros", unit: "money", currency: "EUR", amount: 500, spent: 100, percent: 20 }),
      budget({ id: "b-chf", name: "Francs", unit: "money", currency: "CHF", amount: 500, spent: 0, percent: 0 }),
    ]);
    view();

    const sales = await screen.findByRole("listitem", { name: "Sales per month" });
    expect(within(sales).getByText(/teams below add up to more than this one/)).toBeInTheDocument();
    const euros = screen.getByRole("listitem", { name: "Euros" });
    expect(await within(euros).findByText(/Usage of models priced in USD does not count/)).toBeInTheDocument();
    expect(within(euros).getByText("€100.00 of €500.00")).toBeInTheDocument();
    const francs = screen.getByRole("listitem", { name: "Francs" });
    expect(within(francs).getByText(/No model has a price in CHF/)).toBeInTheDocument();
  });

  it("shows a person's budget with its limit only: what they use is theirs (M17)", async () => {
    budgets.list.mockResolvedValue([
      budget({ id: "b-p", name: "One person", scope: "person", target_id: "u-9", target_label: null, spent: null, percent: null, suppressed: true }),
    ]);
    view();

    const row = await screen.findByRole("listitem", { name: "One person" });
    expect(within(row).getByText("Person · A person")).toBeInTheDocument();
    expect(within(row).getByText(/shown only to them\. Administrators see the limit\./)).toBeInTheDocument();
    expect(within(row).getByText("Limit 1,000,000 tokens")).toBeInTheDocument();
    expect(within(row).queryByRole("progressbar")).toBeNull();
  });

  it("deletes a budget after a confirmation", async () => {
    budgets.remove.mockResolvedValue(undefined);
    view();

    await userEvent.click(await screen.findByRole("button", { name: "Delete Sales per month" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/no longer limits anything/)).toBeInTheDocument();
    budgets.list.mockResolvedValue([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(budgets.remove).toHaveBeenCalledWith("b-sales"));
    expect(await screen.findByText("Sales per month is deleted.")).toBeInTheDocument();
    expect(await screen.findByText(/No budgets yet/)).toBeInTheDocument();
  });

  it("opens the dialog for a new budget", async () => {
    view();

    await screen.findByRole("listitem", { name: "Sales per month" });
    await userEvent.click(screen.getByRole("button", { name: "New budget" }));
    const dialog = await screen.findByRole("dialog", { name: "New budget" });
    expect(within(dialog).getByText(/must have room/)).toBeInTheDocument();
  });

  it("opens the chargeback and the settings as tabs", async () => {
    budgets.chargeback.mockResolvedValue({ month: "2026-09", by: "team", min_group_size: 5, rows: [] });
    view("/admin/budgets?tab=chargeback");

    expect(await screen.findByRole("tab", { name: "Chargeback", selected: true })).toBeInTheDocument();
    expect(await screen.findByText("An internal estimate")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Settings" }));
    expect(await screen.findByRole("switch", { name: "Person budgets" })).toBeInTheDocument();
  });

  it("shows nothing while the server does not offer budgets: the address leads to Usage", async () => {
    vi.mocked(hasFeature).mockResolvedValue(false);
    view();

    expect(await screen.findByText("The usage page")).toBeInTheDocument();
    expect(budgets.list).not.toHaveBeenCalled();
    expect(screen.queryByText("Budgets")).toBeNull();
  });
});
