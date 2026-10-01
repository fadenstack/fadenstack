import i18n from "i18next";

/** A date and time in the reader's language; `fallback` when there is none, the text itself when it is no date. */
export function formatDateTime(iso: string | null | undefined, fallback = "—"): string {
  if (!iso) return fallback;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  // The reader's language, not the browser's default locale.
  return parsed.toLocaleString(i18n.language || undefined);
}

/** A day label ("2026-09-26") as a local date: not UTC midnight, which is the day before west of Greenwich. */
export function localDay(label: string): Date {
  const [y, m, d] = label.split("-").map(Number);
  return new Date(y, m - 1, d);
}
