/**
 * Formatting for the budget pages: amounts in tokens or money, the share used, the period, the refusals
 * worded. In the UI language; the words come from the `budgets.*` keys.
 */
import { fmtInt, fmtMoney } from "~/components/dashboard/format";
import { ApiError, errorText } from "~/api/http";
import type { BudgetUnit } from "~/api/budgets";
import { fmtDay } from "~/lib/updateNotice";

type T = (key: string, options?: Record<string, unknown>) => string;

/** An amount as a number with its unit: "1,000,000 tokens", "€250.00". */
export function fmtBudgetAmount(value: number, unit: BudgetUnit, currency: string | null, t: T, lang?: string): string {
  if (unit === "money" && currency) return fmtMoney(value, currency, lang);
  return t("budgets.amount_tokens", { amount: fmtInt(value, lang) });
}

/** "250,000 of 1,000,000 tokens", "€250.00 of €1,000.00". */
export function fmtUsed(spent: number, amount: number, unit: BudgetUnit, currency: string | null, t: T, lang?: string): string {
  if (unit === "money" && currency) {
    return t("budgets.used_money", { spent: fmtMoney(spent, currency, lang), amount: fmtMoney(amount, currency, lang) });
  }
  return t("budgets.used_tokens", { spent: fmtInt(spent, lang), amount: fmtInt(amount, lang) });
}

/** A share in percent, whole above 10, one decimal below ("4.5 %", "82 %"). */
export function fmtPercent(percent: number, lang?: string): string {
  const digits = percent < 10 ? 1 : 0;
  return `${new Intl.NumberFormat(lang, { maximumFractionDigits: digits }).format(percent)} %`;
}

/** The share used, for a bar: 0 to 100. */
export function barValue(percent: number | null): number {
  if (percent === null || !Number.isFinite(percent)) return 0;
  return Math.max(0, Math.min(percent, 100));
}

/** A bar's colour: past the amount red, past 80 % amber. */
export function barColor(percent: number | null): "primary" | "warning" | "error" {
  if (percent !== null && percent >= 100) return "error";
  if (percent !== null && percent >= 80) return "warning";
  return "primary";
}

/** A day or a moment, as the reader writes dates. */
export function fmtDate(value: string | null | undefined, lang?: string): string {
  return fmtDay(value ?? null, lang);
}

/** A refusal the console words itself (`budgets.errors.<code>`), else the server's message. */
export function budgetErrorText(err: unknown, t: T): string {
  if (err instanceof ApiError && err.code === "root_mode") return t("budgets.errors.root_mode");
  if (err instanceof ApiError && err.status === 403) return t("budgets.errors.forbidden");
  return errorText(err);
}

/** The kinds of budget a refusal names (`error.param` of `budget_exceeded`). */
export const REFUSAL_KINDS = ["personal", "team", "agent", "organisation"] as const;

/**
 * The words for a refusal by a budget, from the kind the gateway names (never its English message, and
 * never a figure): `budgets.refused.reason.<kind>`, or the general wording for a kind not known here.
 */
export function budgetRefusalKey(param: string | null | undefined): string {
  const kind = (REFUSAL_KINDS as readonly string[]).includes(param ?? "") ? param : "other";
  return `budgets.refused.reason.${kind}`;
}

/** The months a report can be asked for, newest first: this month and the twelve before (`YYYY-MM`). */
export function recentMonths(count = 13, now = new Date()): string[] {
  const months: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const d = new Date(Date.UTC(now.getFullYear(), now.getMonth() - i, 1));
    months.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
  }
  return months;
}

/** A month (`YYYY-MM`) as "October 2026". */
export function fmtMonth(month: string, lang?: string): string {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  try {
    return new Intl.DateTimeFormat(lang, { month: "long", year: "numeric", timeZone: "UTC" }).format(Date.UTC(y, m - 1, 1));
  } catch {
    return month;
  }
}
