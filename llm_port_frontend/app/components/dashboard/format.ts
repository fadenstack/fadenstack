/** Number and time formatting for dashboard widgets, in the UI language. */

export function fmtBytes(value: number | null | undefined, lang?: string): string {
  if (value == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  const num = new Intl.NumberFormat(lang, { maximumFractionDigits: i === 0 ? 0 : 1 }).format(n);
  return `${num} ${units[i]}`;
}

export function fmtPct(value: number | null | undefined, lang?: string, digits = 1): string {
  if (value == null) return "–";
  return new Intl.NumberFormat(lang, { maximumFractionDigits: digits }).format(value) + " %";
}

/** 0.0234 -> "2.3 %". */
export function fmtShare(value: number | null | undefined, lang?: string, digits = 1): string {
  if (value == null) return "–";
  return new Intl.NumberFormat(lang, {
    style: "percent",
    maximumFractionDigits: digits,
  }).format(value);
}

/** 14812 -> "14.8K" (or the language's own compact form). */
export function fmtCompact(value: number | null | undefined, lang?: string): string {
  if (value == null) return "–";
  return new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function fmtInt(value: number | null | undefined, lang?: string): string {
  if (value == null) return "–";
  return new Intl.NumberFormat(lang).format(value);
}

export function fmtMoney(amount: string | number, currency: string, lang?: string): string {
  const n = typeof amount === "string" ? Number(amount) : amount;
  try {
    return new Intl.NumberFormat(lang, { style: "currency", currency, maximumFractionDigits: 2 }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export function fmtMs(value: number | null | undefined, lang?: string): string {
  if (value == null) return "–";
  if (value >= 1000) {
    return `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(value / 1000)} s`;
  }
  return `${new Intl.NumberFormat(lang, { maximumFractionDigits: 0 }).format(value)} ms`;
}

/** "12 s ago", "3 min ago" … from a timestamp (ms or ISO). */
export function fmtAgo(when: number | string | null | undefined, lang?: string, now = Date.now()): string {
  if (when == null) return "–";
  const t = typeof when === "number" ? when : Date.parse(when);
  if (Number.isNaN(t)) return "–";
  const seconds = Math.round((t - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "short" });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}
