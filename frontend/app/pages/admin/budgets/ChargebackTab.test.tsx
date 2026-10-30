/**
 * Chargeback: a month per team (indented by the tree) or per cost centre, every row at least k people,
 * the merged row last, money labelled an internal estimate (M19), and the CSV download for the same.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "~/api/budgets";
import { recentMonths } from "~/components/budgets/budgetFormat";
import { renderPage } from "~/test/renderPage";
import { chargeback } from "~/test/budgetFixtures";

import ChargebackTab, { chargebackOrder } from "./ChargebackTab";

vi.mock("~/api/budgets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/budgets")>();
  return { ...actual, budgetsApi: { ...actual.budgetsApi, chargeback: vi.fn() } };
});

const report = vi.mocked(api.budgetsApi.chargeback);
const lastMonth = recentMonths()[1];

beforeEach(() => {
  report.mockReset();
  report.mockResolvedValue(chargeback());
});

/** The first cell of each row of the table. */
function names(): string[] {
  const table = screen.getByRole("table", { name: "Chargeback" });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent ?? "");
}

describe("chargeback", () => {
  it("shows last month per team, labelled an internal estimate, with every row at least k people", async () => {
    const { container } = renderPage(<ChargebackTab />);

    await screen.findByText("Company");
    expect(report).toHaveBeenCalledWith(lastMonth, "team");
    expect(names()).toEqual(["Company", "Sales", "Fewer than 5 people"]);
    const merged = within(screen.getByRole("table", { name: "Chargeback" })).getAllByRole("row")[3];
    expect(within(merged).getByText("5 or more")).toBeInTheDocument();
    expect(within(merged).getByText("€1.00")).toBeInTheDocument();
    const estimate = container.querySelector("[data-budget-estimate]") as HTMLElement;
    expect(estimate).toHaveTextContent("An internal estimate");
    expect(estimate).toHaveTextContent("not a provider's bill");
    expect(estimate).toHaveTextContent("at least 5 people");
    expect(estimate).toHaveTextContent("FinOps FOCUS");
  });

  it("downloads the same report as CSV", async () => {
    renderPage(<ChargebackTab />);

    await screen.findByText("Company");
    expect(screen.getByRole("link", { name: "Download CSV" })).toHaveAttribute(
      "href",
      `/api/ee/budgets/chargeback?month=${lastMonth}&by=team&format=csv`,
    );
  });

  it("asks again per cost centre, and the CSV follows", async () => {
    renderPage(<ChargebackTab />);
    await screen.findByText("Company");
    report.mockResolvedValue(
      chargeback({
        by: "cost_center",
        rows: [
          { key: "4712", label: "4712", other: false, people: 9, input_tokens: 10, output_tokens: 1, cost: { EUR: 2 } },
          { key: null, label: null, other: false, people: 6, input_tokens: 10, output_tokens: 1, cost: {} },
          { key: "4711", label: "4711", other: false, people: 7, input_tokens: 10, output_tokens: 1, cost: { EUR: 1 } },
        ],
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Cost centre" }));

    await waitFor(() => expect(report).toHaveBeenLastCalledWith(lastMonth, "cost_center"));
    await screen.findByText("4711");
    expect(names()).toEqual(["4711", "4712", "No cost centre"]);
    expect(screen.getByText("No price")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download CSV" })).toHaveAttribute(
      "href",
      `/api/ee/budgets/chargeback?month=${lastMonth}&by=cost_center&format=csv`,
    );
  });

  it("offers this month and the twelve before", async () => {
    renderPage(<ChargebackTab />);
    await screen.findByText("Company");

    await userEvent.click(screen.getByRole("combobox", { name: /^Month/ }));
    expect(await screen.findAllByRole("option")).toHaveLength(13);
  });

  it("keeps the tree's order by team, and puts the rows without a name last", () => {
    const rows = chargeback().rows;
    expect(chargebackOrder([rows[2], rows[1], rows[0]], "team").map((r) => r.key ?? "other")).toEqual([
      "g-sales",
      "g-company",
      "other",
    ]);
  });
});
