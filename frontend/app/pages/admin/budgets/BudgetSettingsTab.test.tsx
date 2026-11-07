/**
 * The budget settings: budgets for single people, off by default, locked without root mode and saying
 * what turning them on means (M17, M18); the refusal without root mode worded; and the cost-centre rule
 * with its rules table, each cost centre once.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Group } from "~/api/admin";
import * as api from "~/api/budgets";
import { ApiError } from "~/api/http";
import { renderPage } from "~/test/renderPage";
import { budgetSettings } from "~/test/budgetFixtures";

import BudgetSettingsTab, { rulesProblem } from "./BudgetSettingsTab";

vi.mock("~/api/budgets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/budgets")>();
  return { ...actual, budgetsApi: { ...actual.budgetsApi, settings: vi.fn(), updateSettings: vi.fn() } };
});

const settings = vi.mocked(api.budgetsApi.settings);
const save = vi.mocked(api.budgetsApi.updateSettings);

const TEAMS = [
  { id: "g-sales", name: "Sales", parent_id: null },
  { id: "g-north", name: "North", parent_id: "g-sales" },
] as Group[];

function view(rootModeActive: boolean) {
  const onSaved = vi.fn();
  renderPage(<BudgetSettingsTab teams={TEAMS} onSaved={onSaved} />, { context: { rootModeActive } });
  return { onSaved };
}

beforeEach(() => {
  settings.mockReset();
  save.mockReset();
  settings.mockResolvedValue(budgetSettings());
});

describe("budgets for single people", () => {
  it("are off by default and locked without root mode", async () => {
    view(false);

    const toggle = await screen.findByRole("switch", { name: "Person budgets" });
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Off \(the default\): budgets apply to the organisation, teams and agents only/)).toBeInTheDocument();
    expect(screen.getByText("Turn on root mode to change this switch.")).toBeInTheDocument();
    expect(screen.getByText(/agree it with them first \(co-determination under § 87\(1\) no\. 6 BetrVG\)/)).toBeInTheDocument();
  });

  it("say what turning them on means, and save with root mode", async () => {
    save.mockResolvedValue(budgetSettings({ person_budgets: true }));
    const { onSaved } = view(true);

    const toggle = await screen.findByRole("switch", { name: "Person budgets" });
    expect(toggle).toBeEnabled();
    await userEvent.click(toggle);
    const meaning = screen.getByText(/^On: administrators can give one person a budget/);
    expect(meaning).toHaveTextContent("Only that person sees what they used of it");
    expect(meaning).toHaveTextContent("only they get its alerts");
    expect(meaning).toHaveTextContent("Administrators see its limit, not the person's use");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith({ person_budgets: true }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ person_budgets: true }));
  });

  it("word the refusal when root mode ended meanwhile", async () => {
    save.mockRejectedValue(new ApiError("root mode", 403, "root_mode"));
    view(true);

    await userEvent.click(await screen.findByRole("switch", { name: "Person budgets" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/needs root mode\. Turn on root mode and save again\./)).toBeInTheDocument();
  });
});

describe("usage groups from cost centres", () => {
  it("adds a rule of cost centre and team, and saves the rule switch with it", async () => {
    save.mockResolvedValue(
      budgetSettings({ cost_center_rule: true, cost_center_rules: [{ cost_center: "4711", group_id: "g-north" }] }),
    );
    view(false);

    await userEvent.click(await screen.findByRole("switch", { name: "Set the usage group from the cost centre" }));
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const save_ = screen.getByRole("button", { name: "Save" });
    // A rule without a cost centre and a team is not saved.
    expect(save_).toBeDisabled();
    expect(screen.getByText("Every rule needs a cost centre and a team.")).toBeInTheDocument();

    const rule = document.querySelector("[data-budget-rule='0']") as HTMLElement;
    await userEvent.type(within(rule).getByRole("textbox", { name: "Cost centre" }), " 4711 ");
    await userEvent.click(within(rule).getByRole("combobox", { name: /^Team/ }));
    await userEvent.click(await screen.findByRole("option", { name: "North" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        cost_center_rule: true,
        cost_center_rules: [{ cost_center: "4711", group_id: "g-north" }],
      }),
    );
  });

  it("lists the saved rules and removes one", async () => {
    settings.mockResolvedValue(
      budgetSettings({
        cost_center_rule: true,
        cost_center_rules: [
          { cost_center: "4711", group_id: "g-north" },
          { cost_center: "4712", group_id: "g-sales" },
        ],
      }),
    );
    save.mockResolvedValue(budgetSettings({ cost_center_rule: true, cost_center_rules: [{ cost_center: "4712", group_id: "g-sales" }] }));
    view(false);

    expect(await screen.findByDisplayValue("4711")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove rule 1" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({ cost_center_rules: [{ cost_center: "4712", group_id: "g-sales" }] }),
    );
  });

  it("takes each cost centre once", () => {
    expect(rulesProblem([{ cost_center: "A1", group_id: "g" }, { cost_center: "a1 ", group_id: "h" }])).toBe("duplicate");
    expect(rulesProblem([{ cost_center: "", group_id: "g" }])).toBe("incomplete");
    expect(rulesProblem([{ cost_center: "A1", group_id: "g" }])).toBeNull();
  });
});
