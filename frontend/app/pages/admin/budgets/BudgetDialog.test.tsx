/**
 * The budget dialog: whom a budget applies to (a team from the tree; a person only while person budgets
 * are on), what the form checks before it sends, what a new budget sends, what an edit sends (only what
 * changed; whom it applies to stays), and each refusal of the server worded under its field.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AdminUser, Group } from "~/api/admin";
import type { Agent } from "~/api/agents";
import * as api from "~/api/budgets";
import { BudgetProblemsError } from "~/api/budgets";
import { getAppTheme } from "~/theme";
import { budget } from "~/test/budgetFixtures";

import BudgetDialog, { checkForm, formFromBudget, type BudgetDialogProps } from "./BudgetDialog";

vi.mock("~/api/budgets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/budgets")>();
  return { ...actual, budgetsApi: { ...actual.budgetsApi, create: vi.fn(), update: vi.fn() } };
});

const create = vi.mocked(api.budgetsApi.create);
const update = vi.mocked(api.budgetsApi.update);

const TEAMS = [
  { id: "g-company", name: "Company", parent_id: null },
  { id: "g-sales", name: "Sales", parent_id: "g-company" },
  { id: "g-north", name: "North", parent_id: "g-sales" },
  { id: "g-old", name: "Old team", parent_id: null, archived_at: "2026-01-01T00:00:00Z" },
] as Group[];
const AGENTS = [{ id: "a-office", name: "Office add-in" }] as Agent[];
const USERS = [
  { id: "u-alex", email: "alex@example.com" },
  { id: "u-kim", email: "kim@example.com" },
] as AdminUser[];

function view(props: Partial<BudgetDialogProps> = {}) {
  const onSaved = vi.fn();
  const onClose = vi.fn();
  render(
    <ThemeProvider theme={getAppTheme("light")}>
      <BudgetDialog
        open
        budget={null}
        personBudgets={false}
        minGroupSize={5}
        personReporting={false}
        teams={TEAMS}
        agents={AGENTS}
        users={USERS}
        currencies={["EUR"]}
        onClose={onClose}
        onSaved={onSaved}
        {...props}
      />
    </ThemeProvider>,
  );
  return { onSaved, onClose, dialog: screen.getByRole("dialog") };
}

/** Opens a select by its label and picks an option. */
async function pick(dialog: HTMLElement, label: string, option: string) {
  await userEvent.click(within(dialog).getByRole("combobox", { name: new RegExp(`^${label}`) }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

beforeEach(() => {
  create.mockReset();
  update.mockReset();
});

describe("whom a budget applies to", () => {
  it("offers the organisation, a team and an agent; a person only while person budgets are on", async () => {
    const { dialog } = view();

    await userEvent.click(within(dialog).getByRole("combobox", { name: /^Applies to/ }));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(options).toEqual(["Organisation", "Team", "Agent"]);
    await userEvent.keyboard("{Escape}");
    expect(within(dialog).getByText(/Budgets for single people are off/)).toBeInTheDocument();
  });

  it("offers a person while person budgets are on, and says only they see what they use", async () => {
    const { dialog } = view({ personBudgets: true });

    await pick(dialog, "Applies to", "Person");
    expect(within(dialog).getByText(/Only this person's requests count/)).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Person" })).toBeInTheDocument();
    expect(within(dialog).queryByText(/Budgets for single people are off/)).toBeNull();
  });

  it("picks a team from the tree, sub-teams below their parent, archived teams left out", async () => {
    const { dialog } = view();

    await userEvent.click(within(dialog).getByRole("combobox", { name: /^Team/ }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Company", "Sales", "North"]);
    await userEvent.keyboard("{Escape}");
    expect(within(dialog).getByText(/needs at least 5 people, counting its sub-teams/)).toBeInTheDocument();
  });

  it("says a team of any size may have a budget while person-level reporting is on", () => {
    const { dialog } = view({ personReporting: true });
    expect(within(dialog).getByText(/a team of any size can have a budget/)).toBeInTheDocument();
  });
});

describe("what the form checks before it sends", () => {
  it("asks for a name, a target and an amount", async () => {
    const { dialog } = view();
    await userEvent.clear(within(dialog).getByRole("textbox", { name: "Alerts at (%)" }));
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Alerts at (%)" }), "80, lots");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(create).not.toHaveBeenCalled();
    // The name and the amount.
    expect(within(dialog).getAllByText("Required.")).toHaveLength(2);
    expect(within(dialog).getByText("Choose whom it applies to.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Whole percentages from 1 to 1000/)).toBeInTheDocument();
    expect(within(dialog).getByText("Some fields need a look.")).toBeInTheDocument();
  });

  it("checks a money amount and its currency", () => {
    const form = { ...formFromBudget(null), name: "x", scope: "tenant" as const, unit: "money" as const, amount: "12.34567", currency: "EURO" };
    expect(checkForm(form).problems).toEqual([
      { field: "currency", code: "invalid" },
      { field: "amount", code: "invalid" },
    ]);
  });
});

describe("saving", () => {
  it("sends a new team budget as the API takes it", async () => {
    create.mockResolvedValue(budget({ id: "b-new", name: "North per week" }));
    const { dialog, onSaved } = view();

    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "North per week");
    await pick(dialog, "Team", "North");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Amount" }), "500000");
    await pick(dialog, "Period", "Per week");
    await userEvent.click(within(dialog).getByRole("radio", { name: /Soft/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith({
      name: "North per week",
      scope: "team",
      target_id: "g-north",
      unit: "tokens",
      currency: null,
      amount: 500000,
      period: "week",
      mode: "soft",
      thresholds: [80, 100],
      owner_id: null,
    });
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "b-new" }));
  });

  it("sends a money budget with its currency and an owner", async () => {
    create.mockResolvedValue(budget());
    const { dialog } = view();

    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Everyone");
    await pick(dialog, "Applies to", "Organisation");
    await userEvent.click(within(dialog).getByRole("button", { name: "Money" }));
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Amount" }), "2500,50");
    await userEvent.click(within(dialog).getByRole("combobox", { name: "Owner" }));
    await userEvent.click(await screen.findByRole("option", { name: "kim@example.com" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({
      scope: "tenant",
      target_id: null,
      unit: "money",
      currency: "EUR",
      amount: 2500.5,
      owner_id: "u-kim",
    });
  });

  it("sends only what an edit changed, and keeps whom it applies to", async () => {
    update.mockResolvedValue(budget({ amount: 2000000 }));
    const { dialog } = view({ budget: budget() });

    expect(screen.getByRole("dialog", { name: "Edit budget" })).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: /^Applies to/ })).toHaveAttribute("aria-disabled", "true");
    expect(within(dialog).getByRole("combobox", { name: /^Team/ })).toHaveAttribute("aria-disabled", "true");
    const amount = within(dialog).getByRole("textbox", { name: "Amount" });
    await userEvent.clear(amount);
    await userEvent.type(amount, "2000000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("b-sales", { amount: 2000000 }));
  });
});

describe("the server's refusals, worded", () => {
  async function refuse(problems: { field: string; code: string }[], props: Partial<BudgetDialogProps> = {}) {
    create.mockRejectedValue(new BudgetProblemsError(problems));
    const { dialog } = view(props);
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Small team");
    await pick(dialog, "Team", "North");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Amount" }), "1000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    return dialog;
  }

  it("a team too small for a budget of its own, with k", async () => {
    const dialog = await refuse([{ field: "target_id", code: "team_too_small" }], { minGroupSize: 7 });
    expect(await within(dialog).findByText(/fewer than 7 people, counting its sub-teams/)).toBeInTheDocument();
    expect(within(dialog).getByText(/needs person-level reporting on/)).toBeInTheDocument();
  });

  it("a budget larger than its parent team's", async () => {
    const dialog = await refuse([{ field: "amount", code: "exceeds_parent" }]);
    expect(await within(dialog).findByText(/Larger than the budget of the team above it/)).toBeInTheDocument();
  });

  it("one that exists already, named for no field of the form", async () => {
    const dialog = await refuse([{ field: "budget", code: "duplicate" }]);
    const alert = await within(dialog).findByText(/exists already/);
    expect(alert.closest("[data-budget-dialog-error]")).not.toBeNull();
  });

  it("a personal agent while person budgets are off", async () => {
    create.mockRejectedValue(new BudgetProblemsError([{ field: "target_id", code: "person_budgets_off" }]));
    const { dialog } = view();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "My agent");
    await pick(dialog, "Applies to", "Agent");
    await pick(dialog, "Agent", "Office add-in");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Amount" }), "1000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByText(/Only one person uses this agent/)).toBeInTheDocument();
  });

  it("person budgets switched off since", async () => {
    const dialog = await refuse([{ field: "scope", code: "person_budgets_off" }]);
    expect(await within(dialog).findByText(/Switch them on under Settings on this page, in root mode/)).toBeInTheDocument();
  });
});
