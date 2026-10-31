/**
 * Budgets (`/api/ee/budgets`, for administrators): how many tokens, or how much money, the organisation, a
 * team (with everything below it in the team tree), an agent or one person may use per day, week or month;
 * what each has used of it; the settings; and the chargeback report per team or cost centre. Offered while
 * the server lists the feature {@link BUDGETS} (`GET /api/about`).
 *
 * The answers already follow the privacy rules; the console shows what it gets:
 * - a person's budget: administrators see its limit, not what the person used (`suppressed`, `spent` null),
 *   and not whose it is (`target_label` null), unless person-level reporting is on and they hold the usage
 *   auditor role;
 * - a chargeback row covers at least k people; money is an internal estimate.
 *
 * A write the server refuses names the fields (422 `{"detail": {"problems": [{"field", "code"}]}}`, codes
 * {@link BUDGET_PROBLEM_CODES}); switching person budgets needs root mode (403 `root_mode`). Amounts may come
 * as decimal strings: every figure is read as a number here.
 */
import { ApiError, json, query, readError, segment } from "./http";

const BASE = "/api/ee/budgets";

/** The feature the Budgets page and the budgets on My usage show for. */
export const BUDGETS = "budgets";

export type BudgetScope = "tenant" | "team" | "agent" | "person";
export type BudgetUnit = "tokens" | "money";
export type BudgetPeriod = "day" | "week" | "month";
export type BudgetMode = "hard" | "soft" | "shadow";

export const BUDGET_SCOPES: BudgetScope[] = ["tenant", "team", "agent", "person"];
export const BUDGET_UNITS: BudgetUnit[] = ["tokens", "money"];
export const BUDGET_PERIODS: BudgetPeriod[] = ["day", "week", "month"];
export const BUDGET_MODES: BudgetMode[] = ["hard", "soft", "shadow"];

/** The alert thresholds a new budget starts with, in percent of its amount. */
export const DEFAULT_THRESHOLDS = [80, 100];

export interface Budget {
  id: string;
  name: string;
  scope: BudgetScope;
  /** The team, agent or person; null for the organisation. */
  target_id: string | null;
  /** The team's, agent's or person's name; null for the organisation, and for a person the caller may not name. */
  target_label: string | null;
  unit: BudgetUnit;
  /** Money only: the currency it counts (costs priced in another currency do not count). */
  currency: string | null;
  amount: number;
  /** Calendar periods in the usage time zone; a week is an ISO week (Monday to Sunday). */
  period: BudgetPeriod;
  mode: BudgetMode;
  /** Alerts at these percentages of the amount, once each per period. */
  thresholds: number[];
  /** Who gets the alerts besides the administrators. */
  owner_id: string | null;
  /** The current period, first and last day (`YYYY-MM-DD`). */
  period_start: string;
  period_end: string;
  /** Used in the current period; null where the caller may not see it (`suppressed`). */
  spent: number | null;
  reserved: number | null;
  /** `spent` in percent of `amount`. */
  percent: number | null;
  /** At the rate so far, the day it runs out before the period ends; null when it lasts (or nothing is known). */
  forecast_end: string | null;
  /**
   * Shadow budgets: requests this period that a hard budget would have refused; null when it is not known
   * (not a shadow budget, its figures hidden from the caller, or the gateway's counters unreachable).
   */
  would_refuse: number | null;
  /** The budgets of the teams below add up to more than this one (each still holds). */
  children_exceed: boolean;
  /**
   * Its use is hidden from the caller: a person's budget (unless person-level reporting is on and they hold
   * the usage auditor role), or a team's or the organisation's that fewer than k people used this period.
   */
  suppressed: boolean;
}

/** What a create sends, and what an edit may change. */
export interface BudgetWrite {
  name: string;
  scope: BudgetScope;
  target_id: string | null;
  unit: BudgetUnit;
  currency: string | null;
  amount: number;
  period: BudgetPeriod;
  mode: BudgetMode;
  thresholds: number[];
  owner_id: string | null;
}

/** A cost centre (as provisioning sends it for a person) and the team it makes their usage group. */
export interface CostCenterRule {
  cost_center: string;
  group_id: string;
}

export interface BudgetSettings {
  /** Budgets for single people (off by default); switching it needs root mode. */
  person_budgets: boolean;
  /** Provisioning sets a person's usage group from their cost centre, by the rules. */
  cost_center_rule: boolean;
  cost_center_rules: CostCenterRule[];
}

export type ChargebackBy = "team" | "cost_center";

/**
 * One row of the chargeback report. By team, a team's figures include its sub-teams' (`parent_key`, `path`
 * place it in the tree, top first). `other`: the row everything with fewer than k people was merged into.
 * `key` null and not `other`: no team ("Unassigned"), or no cost centre.
 */
export interface ChargebackRow {
  key: string | null;
  label: string | null;
  other: boolean;
  parent_key?: string | null;
  path?: string[];
  people: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  /** Per currency; `{}` where no price applies. */
  cost: Record<string, number> | null;
}

export interface Chargeback {
  /** `YYYY-MM`. */
  month: string;
  by: ChargebackBy;
  /** k: the fewest people a row covers. */
  min_group_size: number;
  rows: ChargebackRow[];
}

// ── My usage: the person's own budgets (`usage_sections`, kind "budgets") ─────

/** Whose budget it is, as the person reads it. */
export type MyBudgetKind = "personal" | "team" | "agent" | "organisation";

export interface MyBudget {
  name: string;
  kind: MyBudgetKind;
  unit: BudgetUnit;
  currency: string | null;
  amount: number;
  /** Personal budgets: always; a team's or the organisation's only when enough people share it. */
  spent: number | null;
  percent: number | null;
  period_end: string;
  mode: BudgetMode;
}

/** An alert of this period: a threshold crossed, or a forecast that the budget runs out before the period ends. */
export interface MyBudgetAlert {
  name: string;
  kind: "threshold" | "forecast";
  /** Percent of the amount; null for a forecast. */
  threshold: number | null;
  at: string;
}

export interface MyBudgets {
  budgets: MyBudget[];
  alerts: MyBudgetAlert[];
}

// ── Reading the answers ──────────────────────────────────────────────────────

/** A figure that may come as a decimal string; null when there is none. */
export function figure(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function costOf(value: unknown): Record<string, number> | null {
  if (!value || typeof value !== "object") return null;
  const out: Record<string, number> = {};
  for (const [currency, amount] of Object.entries(value as Record<string, unknown>)) {
    const n = figure(amount);
    if (n !== null) out[currency] = n;
  }
  return out;
}

type Raw<T> = { [K in keyof T]?: unknown };

export function readBudget(raw: Raw<Budget>): Budget {
  return {
    id: String(raw.id),
    name: String(raw.name ?? ""),
    scope: raw.scope as BudgetScope,
    target_id: (raw.target_id as string | null) ?? null,
    target_label: (raw.target_label as string | null) ?? null,
    unit: raw.unit as BudgetUnit,
    currency: (raw.currency as string | null) ?? null,
    amount: figure(raw.amount) ?? 0,
    period: raw.period as BudgetPeriod,
    mode: raw.mode as BudgetMode,
    thresholds: Array.isArray(raw.thresholds) ? raw.thresholds.map(Number).filter(Number.isFinite) : [],
    owner_id: (raw.owner_id as string | null) ?? null,
    period_start: String(raw.period_start ?? ""),
    period_end: String(raw.period_end ?? ""),
    spent: figure(raw.spent),
    reserved: figure(raw.reserved),
    percent: figure(raw.percent),
    forecast_end: (raw.forecast_end as string | null) ?? null,
    would_refuse: figure(raw.would_refuse),
    children_exceed: raw.children_exceed === true,
    suppressed: raw.suppressed === true,
  };
}

function readChargeback(raw: Raw<Chargeback>): Chargeback {
  const rows = Array.isArray(raw.rows) ? (raw.rows as Raw<ChargebackRow>[]) : [];
  return {
    month: String(raw.month ?? ""),
    by: raw.by === "cost_center" ? "cost_center" : "team",
    min_group_size: figure(raw.min_group_size) ?? 5,
    rows: rows.map((row) => ({
      key: (row.key as string | null) ?? null,
      label: (row.label as string | null) ?? null,
      other: row.other === true,
      parent_key: (row.parent_key as string | null | undefined) ?? null,
      path: Array.isArray(row.path) ? row.path.map(String) : undefined,
      people: figure(row.people),
      input_tokens: figure(row.input_tokens),
      output_tokens: figure(row.output_tokens),
      cost: costOf(row.cost),
    })),
  };
}

const MY_KINDS: MyBudgetKind[] = ["personal", "team", "agent", "organisation"];

/** The `budgets` block of My usage, read defensively: what does not fit is left out. */
export function readMyBudgets(data: Record<string, unknown> | null | undefined): MyBudgets {
  const source = data ?? {};
  const budgets = Array.isArray(source.budgets) ? (source.budgets as Raw<MyBudget>[]) : [];
  const alerts = Array.isArray(source.alerts) ? (source.alerts as Raw<MyBudgetAlert>[]) : [];
  return {
    budgets: budgets
      .filter((b) => b && typeof b === "object")
      .map((b) => ({
        name: String(b.name ?? ""),
        kind: MY_KINDS.includes(b.kind as MyBudgetKind) ? (b.kind as MyBudgetKind) : "organisation",
        unit: b.unit === "money" ? "money" : "tokens",
        currency: (b.currency as string | null) ?? null,
        amount: figure(b.amount) ?? 0,
        spent: figure(b.spent),
        percent: figure(b.percent),
        period_end: String(b.period_end ?? ""),
        mode: BUDGET_MODES.includes(b.mode as BudgetMode) ? (b.mode as BudgetMode) : "hard",
      })),
    alerts: alerts
      .filter((a) => a && typeof a === "object")
      .map((a) => ({
        name: String(a.name ?? ""),
        kind: a.kind === "forecast" ? ("forecast" as const) : ("threshold" as const),
        threshold: a.kind === "forecast" ? null : figure(a.threshold),
        at: String(a.at ?? ""),
      })),
  };
}

// ── The rules the form checks before it sends (the server checks them again) ──

/** The codes of a refused write, each worded in the console (`budgets.problems.<code>`). */
export const BUDGET_PROBLEM_CODES = [
  "required",
  "invalid",
  "team_too_small",
  "exceeds_parent",
  "person_budgets_off",
  "duplicate",
] as const;

export interface BudgetProblem {
  field: string;
  code: string;
}

/** A refused write that names the fields (422 `problems`). */
export class BudgetProblemsError extends ApiError {
  constructor(readonly problems: BudgetProblem[]) {
    super(problems.map((p) => `${p.field}: ${p.code}`).join("; "), 422);
    this.name = "BudgetProblemsError";
  }
}

/** A budget's name: at most this long. */
export const NAME_MAX = 128;

/** An amount as typed: a whole number of tokens, or money with up to four decimals (point or comma). */
export function parseAmount(raw: string, unit: BudgetUnit): number | null {
  const text = raw.trim().replace(/\s/g, "");
  const ok = unit === "tokens" ? /^\d+$/.test(text) : /^\d+(?:[.,]\d{1,4})?$/.test(text);
  if (!ok) return null;
  const n = Number(text.replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Alert thresholds as typed ("80, 100"): whole percentages from 1 to 1000, each once, in order; null if not. */
export function parseThresholds(raw: string): number[] | null {
  const parts = raw
    .split(/[,;\s]+/)
    .map((p) => p.trim().replace(/%$/, ""))
    .filter(Boolean);
  const values: number[] = [];
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return null;
    const n = Number(part);
    if (n < 1 || n > 1000) return null;
    if (!values.includes(n)) values.push(n);
  }
  return values.sort((a, b) => a - b);
}

/** A currency code: three letters (ISO 4217). */
export function currencyOk(value: string): boolean {
  return /^[A-Z]{3}$/.test(value.trim().toUpperCase());
}

// ── Calls ────────────────────────────────────────────────────────────────────

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    credentials: "include",
  });
  if (!res.ok) {
    const problems = await res
      .clone()
      .json()
      .then((body: { detail?: { problems?: BudgetProblem[] } }) => body?.detail?.problems)
      .catch(() => undefined);
    if (Array.isArray(problems)) throw new BudgetProblemsError(problems);
    throw await readError(res);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const budgetsApi = {
  /** Every budget with its current period, ordered as the server gives them. */
  list: () => request<Raw<Budget>[]>("").then((list) => list.map(readBudget)),
  create: (body: BudgetWrite) => request<Raw<Budget>>("", json("POST", body)).then(readBudget),
  /** Only the fields given change. */
  update: (id: string, changes: Partial<BudgetWrite>) =>
    request<Raw<Budget>>(`/${segment(id)}`, json("PATCH", changes)).then(readBudget),
  remove: (id: string) => request<void>(`/${segment(id)}`, json("DELETE")),

  settings: () => request<BudgetSettings>("/settings"),
  /** Only the fields given change; `person_budgets` needs root mode. */
  updateSettings: (changes: Partial<BudgetSettings>) => request<BudgetSettings>("/settings", json("PUT", changes)),

  /** A month's usage and estimated cost per team (rolled up the tree) or per cost centre. */
  chargeback: (month: string, by: ChargebackBy) =>
    request<Raw<Chargeback>>(`/chargeback${query({ month, by, format: "json" })}`).then(readChargeback),
  /** Where the browser downloads the same as CSV, with FOCUS column names. */
  chargebackCsvUrl: (month: string, by: ChargebackBy) => `${BASE}/chargeback${query({ month, by, format: "csv" })}`,
};
