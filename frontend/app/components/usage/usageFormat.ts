/**
 * Formatting for the usage pages: periods, money per currency, the date range
 * a period choice stands for. In the UI language; nothing here is text of its
 * own (labels come from the `usage.*` keys).
 */
import { fmtMoney } from "~/components/dashboard/format";
import { ApiError, errorText } from "~/api/http";
import { isUsageErrorCode, type AdminBucket } from "~/api/usage";

/** A refusal the console words itself (`usage.errors.<code>`), else the server's message. */
export function usageErrorText(err: unknown, t: (key: string) => string): string {
  if (err instanceof ApiError && isUsageErrorCode(err.code)) return t(`usage.errors.${err.code}`);
  return errorText(err);
}

/** `YYYY-MM-DD` of a date in the browser's time zone. */
export function isoDay(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Today's date (`YYYY-MM-DD`) in `timeZone` (an IANA name); the browser's when none is given or known. */
export function todayIn(timeZone?: string, now = new Date()): string {
  if (timeZone) {
    try {
      const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" })
        .formatToParts(now)
        .reduce<Record<string, string>>((acc, p) => ({ ...acc, [p.type]: p.value }), {});
      return `${parts.year}-${parts.month}-${parts.day}`;
    } catch {
      // An unknown zone: the browser's day.
    }
  }
  return isoDay(now);
}

/**
 * The last `days` days, today included, as the API's `from`/`to`: days of the usage time zone (where
 * the server's days begin), so "today" is the server's today even when the browser is elsewhere.
 */
export function lastDays(days: number, timeZone?: string, now = new Date()): { from: string; to: string } {
  const to = todayIn(timeZone, now);
  const start = new Date(`${to}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return { from: start.toISOString().slice(0, 10), to };
}

/**
 * `from`/`to` (`YYYY-MM-DD`, both included) cut to the last `maxDays` days of the range; `clamped` says
 * whether anything was cut.
 */
export function clampRange(
  range: { from: string; to: string },
  maxDays: number,
): { from: string; to: string; clamped: boolean } {
  const first = Date.parse(`${range.from}T00:00:00Z`);
  const last = Date.parse(`${range.to}T00:00:00Z`);
  if (Number.isNaN(first) || Number.isNaN(last) || maxDays < 1) return { ...range, clamped: false };
  const days = Math.round((last - first) / 86_400_000) + 1;
  if (days <= maxDays) return { ...range, clamped: false };
  const start = new Date(last);
  start.setUTCDate(start.getUTCDate() - (maxDays - 1));
  return { from: start.toISOString().slice(0, 10), to: range.to, clamped: true };
}

/**
 * A period of a series as a Date: an hour is ISO-8601 UTC; a day
 * (`2026-10-01`) or a month (`2026-10`, `2026-10-01`) is a calendar date,
 * read as local midnight so it is written as the same day.
 */
export function periodDate(period: string): Date {
  if (/^\d{4}-\d{2}$/.test(period)) return new Date(`${period}-01T00:00:00`);
  if (/^\d{4}-\d{2}-\d{2}$/.test(period)) return new Date(`${period}T00:00:00`);
  return new Date(period);
}

/** How a period is written on an axis or in a table. */
export function periodLabel(period: string, bucket: AdminBucket, lang?: string): string {
  const date = periodDate(period);
  if (Number.isNaN(date.getTime())) return period;
  const options: Intl.DateTimeFormatOptions =
    bucket === "hour"
      ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
      : bucket === "month"
        ? { month: "short", year: "numeric" }
        : { day: "numeric", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat(lang, options).format(date);
}

/**
 * Estimated cost, each currency on its own ("€1.20 · $0.40"); null when no
 * price applies -- a missing price is not a zero.
 */
export function fmtCost(cost: Record<string, number> | null | undefined, lang?: string): string | null {
  const entries = Object.entries(cost ?? {});
  if (entries.length === 0) return null;
  return entries.map(([currency, amount]) => fmtMoney(amount, currency, lang)).join(" · ");
}

/** A moment as date and time in the UI language. */
export function fmtWhen(iso: string | null | undefined, lang?: string): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
