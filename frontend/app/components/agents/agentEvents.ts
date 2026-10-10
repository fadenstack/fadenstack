/**
 * What an SDK reported, as one line for a table: the tool or server it is
 * about, a few facts, and how it ended. Events carry names, counts and
 * durations, never content.
 */
import type { ClientEvent } from "~/api/agents";

export const EVENT_TYPES = ["tool.executed", "approval.decided", "local_mcp.attached", "session.stats"] as const;

export type EventTone = "success" | "error" | "warning" | "default";

export interface EventSummary {
  /** The tool, server or session it is about. */
  subject: string;
  /** How it ended (`ok`, `denied`, ...), when it has an end. */
  result?: string;
  tone: EventTone;
  /** A duration in ms, when it took one. */
  durationMs?: number;
  /** Where the tool ran (`host`, `mcp`, `gateway`), or other context. */
  facts: string[];
  /** A count shown as "N tools" / "N turns". */
  count?: { key: "tools" | "turns"; value: number };
  /** What let an approved call through: `once`, `chat`, `always` or `bypass` (the app's bypass mode). */
  scope?: string;
}

const TONES: Record<string, EventTone> = {
  ok: "success",
  approved: "success",
  error: "error",
  denied: "warning",
  cancelled: "default",
  timeout: "warning",
};

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value.length > 0 ? value : undefined;
const number = (value: unknown): number | undefined => (typeof value === "number" ? value : undefined);

export function summarizeEvent(event: ClientEvent): EventSummary {
  const d = event.data ?? {};
  const facts = (...values: unknown[]) => values.map(text).filter((v): v is string => v !== undefined);
  const outcome = (result: string | undefined) => ({ result, tone: (result && TONES[result]) || "default" });

  switch (event.type) {
    case "tool.executed":
      return {
        subject: text(d.tool) ?? "?",
        ...outcome(text(d.outcome)),
        durationMs: number(d.duration_ms),
        facts: facts(d.source, d.server),
      };
    case "approval.decided":
      return {
        subject: text(d.tool) ?? "?",
        ...outcome(text(d.decision)),
        durationMs: number(d.wait_ms),
        facts: [],
        scope: text(d.scope),
      };
    case "local_mcp.attached":
      return {
        subject: text(d.label) ?? "?",
        tone: "default",
        facts: facts(d.command ?? d.url),
        count: { key: "tools", value: Array.isArray(d.tools) ? d.tools.length : 0 },
      };
    case "session.stats":
      return {
        subject: event.conversation_id ?? "",
        tone: "default",
        facts: [],
        count: { key: "turns", value: number(d.turns) ?? 0 },
      };
    default:
      return { subject: "", tone: "default", facts: [] };
  }
}
