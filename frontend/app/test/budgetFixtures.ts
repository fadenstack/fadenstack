/** Budget API payloads (B5) for page tests. */
import type { Budget, BudgetSettings, Chargeback } from "~/api/budgets";

export function budget(overrides: Partial<Budget> = {}): Budget {
  return {
    id: "b-sales",
    name: "Sales per month",
    scope: "team",
    target_id: "g-sales",
    target_label: "Sales",
    unit: "tokens",
    currency: null,
    amount: 1000000,
    period: "month",
    mode: "hard",
    thresholds: [80, 100],
    owner_id: null,
    period_start: "2026-10-01",
    period_end: "2026-10-31",
    spent: 250000,
    reserved: null,
    percent: 25,
    forecast_end: null,
    would_refuse: 0,
    children_exceed: false,
    suppressed: false,
    ...overrides,
  };
}

export function budgetSettings(overrides: Partial<BudgetSettings> = {}): BudgetSettings {
  return { person_budgets: false, cost_center_rule: false, cost_center_rules: [], ...overrides };
}

export function chargeback(overrides: Partial<Chargeback> = {}): Chargeback {
  return {
    month: "2026-09",
    by: "team",
    min_group_size: 5,
    rows: [
      {
        key: "g-company",
        label: "Company",
        other: false,
        parent_key: null,
        path: ["g-company"],
        people: 40,
        input_tokens: 900000,
        output_tokens: 120000,
        cost: { EUR: 42.5 },
      },
      {
        key: "g-sales",
        label: "Sales",
        other: false,
        parent_key: "g-company",
        path: ["g-company", "g-sales"],
        people: 12,
        input_tokens: 300000,
        output_tokens: 40000,
        cost: { EUR: 14.2 },
      },
      {
        key: null,
        label: null,
        other: true,
        parent_key: "g-company",
        path: ["g-company", "other"],
        people: null,
        input_tokens: 20000,
        output_tokens: 3000,
        cost: { EUR: 1 },
      },
    ],
    ...overrides,
  };
}
