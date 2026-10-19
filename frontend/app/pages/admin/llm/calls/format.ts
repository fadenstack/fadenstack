/**
 * Formatting for the LLM Calls explorer: durations, token counts, costs, ids.
 */

/** 850 ms, 12.3 s, 4m 02s, 1h 05m: a latency readable at a glance. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) return `${minutes}m ${String(totalSeconds % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/** 950, 12.4k, 3.1M. */
export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs < 1000) return String(Math.round(n));
  const trim = (s: string) => s.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
  if (abs < 1_000_000) return `${trim((n / 1000).toFixed(abs < 10_000 ? 2 : 1))}k`;
  return `${trim((n / 1_000_000).toFixed(2))}M`;
}

/** A cost with as many digits as it needs to say something: $0.000412, $1.24. */
export function formatCost(value: number | null | undefined, currency: string | null = "USD"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const symbol = !currency || currency === "USD" ? "$" : currency === "EUR" ? "€" : `${currency} `;
  if (value === 0) return `${symbol}0`;
  const digits = value >= 1 ? 2 : Math.min(8, Math.max(2, 2 - Math.floor(Math.log10(value))));
  return `${symbol}${value.toFixed(digits)}`;
}

export function formatPercent(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return "—";
  const pct = rate * 100;
  return `${pct > 0 && pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
}

/** The first 8 characters of a long id: enough to tell calls apart in a list. */
export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  return id.length > 12 ? id.slice(0, 8) : id;
}

/** "12 sec. ago", "5 min. ago", "3 hr. ago", or the date when older than a day. */
export function formatAgo(iso: string, now: number = Date.now(), locale?: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, now - then) / 1000;
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
  if (diff < 60) return rtf.format(-Math.round(diff), "second");
  if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (diff < 86_400) return rtf.format(-Math.round(diff / 3600), "hour");
  return new Date(iso).toLocaleString(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "/v1/chat/completions" -> "chat.completions"; "/v1/agents/x/chat/completions" -> "chat.completions". */
export function callName(endpoint: string): string {
  const path = endpoint.replace(/^\/?(v\d+\/)?/, "");
  const agent = /^agents\/[^/]+\/(.+)$/.exec(path);
  return (agent ? agent[1] : path).replace(/\//g, ".") || endpoint;
}

/** Whether the call went through an agent's endpoint. */
export function isAgentCall(endpoint: string, agentId?: string | null): boolean {
  return Boolean(agentId) || /\/agents\/[^/]+\//.test(endpoint);
}

/** The latency band a call falls in, for colouring. */
export function latencyBand(ms: number, p95?: number | null): "fast" | "normal" | "slow" | "outlier" {
  if (p95 && ms > p95 * 1.5 && ms > 5000) return "outlier";
  if (ms >= 60_000) return "slow";
  if (ms >= 10_000) return "normal";
  return "fast";
}

/** Output tokens per second while generating (after the first token, when known). */
export function generationSpeed(
  completionTokens: number | null,
  latencyMs: number,
  ttftMs: number | null,
): number | null {
  if (!completionTokens || completionTokens <= 0) return null;
  const generatingMs = latencyMs - (ttftMs ?? 0);
  // A first token at the very end (tool rounds before the answer) leaves no window to measure.
  if (generatingMs < 250) return null;
  return completionTokens / (generatingMs / 1000);
}
