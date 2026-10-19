/**
 * Where an agent app's tool runs and approvals go on a request's timeline.
 *
 * An app reports them with a time, but some apps send a turn's events in one batch,
 * all stamped with the time of sending: every event then sits after the last round.
 * The order is still right, and the tools ran in the gaps between the model's
 * rounds (a round that ends asking for tools, the tools, the next round). So when
 * the times are batched, the events are laid into those gaps in order, each gap
 * taking as many as its length holds.
 */

import type { LlmCall, LlmClientEvent } from "~/api/llmCalls";

export interface PlacedEvent {
  event: LlmClientEvent;
  /** Epoch ms where its bar starts: an approval's wait, or a tool's run. */
  start: number;
  duration: number;
}

export interface Placement {
  events: PlacedEvent[];
  /** True when the app's own times were not usable and the events were fitted into the gaps. */
  inferred: boolean;
}

/** An approval and the tool run it let through, or a tool run alone: they go into a gap together. */
interface Unit {
  events: LlmClientEvent[];
  length: number;
}

function length(e: LlmClientEvent): number {
  return e.type === "approval.decided" ? (e.wait_ms ?? 0) : (e.duration_ms ?? 0);
}

function units(events: LlmClientEvent[]): Unit[] {
  const out: Unit[] = [];
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    const next = events[i + 1];
    if (e.type === "approval.decided" && next?.type === "tool.executed" && next.tool === e.tool) {
      out.push({ events: [e, next], length: length(e) + length(next) });
      i++;
    } else {
      out.push({ events: [e], length: length(e) });
    }
  }
  return out;
}

/** The app's times are a batch's when most events share the same instant. */
export function isBatched(events: LlmClientEvent[]): boolean {
  if (events.length < 2) return false;
  const counts = new Map<string, number>();
  for (const e of events) counts.set(e.at, (counts.get(e.at) ?? 0) + 1);
  const most = Math.max(...counts.values());
  return most >= 2 && most >= Math.ceil(events.length / 2);
}

function asReported(events: LlmClientEvent[]): PlacedEvent[] {
  return events.map((e) => {
    const at = new Date(e.at).getTime();
    // An approval is reported when decided: its wait comes before.
    return e.type === "approval.decided"
      ? { event: e, start: at - length(e), duration: length(e) }
      : { event: e, start: at, duration: length(e) };
  });
}

export function placeEvents(calls: LlmCall[], events: LlmClientEvent[]): Placement {
  if (!isBatched(events) || calls.length < 2) return { events: asReported(events), inferred: false };

  const rounds = calls
    .map((c) => {
      const end = new Date(c.created_at).getTime();
      return { end, start: end - c.latency_ms, askedForTools: c.finish_reason === "tool_calls" };
    })
    .sort((a, b) => a.start - b.start);
  const allGaps = rounds
    .slice(0, -1)
    .map((r, i) => ({ start: r.end, length: Math.max(0, rounds[i + 1].start - r.end), askedForTools: r.askedForTools }));
  // Tools run after a round that asked for them; if no round says so, any gap may hold them.
  const gaps = allGaps.some((g) => g.askedForTools) ? allGaps.filter((g) => g.askedForTools) : allGaps;
  const queue = units(events);
  const groups = fit(queue, gaps.map((g) => g.length));

  const placed: PlacedEvent[] = [];
  const lay = (unit: Unit, at: number) => {
    let t = at;
    for (const e of unit.events) {
      placed.push({ event: e, start: t, duration: length(e) });
      t += length(e);
    }
    return t;
  };
  if (groups === null) {
    // Too many to fit: in order after the last round.
    let t = rounds[rounds.length - 1].end;
    for (const unit of queue) t = lay(unit, t);
    return { events: placed, inferred: true };
  }
  groups.forEach((group, g) => {
    let t = gaps[g].start;
    for (const unit of group) t = lay(unit, t);
  });
  return { events: placed, inferred: true };
}

const EMPTY_GAP_COST = 1;
const MAX_FIT = 400;

/**
 * Split the units, in order, into one run per gap so that each run's length is as
 * close as it can be to its gap's (relative error; a gap left empty costs 1).
 */
function fit(queue: Unit[], gaps: number[]): Unit[][] | null {
  const n = queue.length;
  const g = gaps.length;
  if (g === 0 || n > MAX_FIT) return null;
  const prefix = [0];
  for (const u of queue) prefix.push(prefix[prefix.length - 1] + u.length);
  const cost = (from: number, to: number, gap: number) =>
    from === to ? EMPTY_GAP_COST : Math.abs(prefix[to] - prefix[from] - gaps[gap]) / Math.max(gaps[gap], 100);
  // best[j][i]: the least cost of the first i units in the first j gaps.
  const best: number[][] = Array.from({ length: g + 1 }, () => Array(n + 1).fill(Infinity));
  const from: number[][] = Array.from({ length: g + 1 }, () => Array(n + 1).fill(0));
  best[0][0] = 0;
  for (let j = 1; j <= g; j++) {
    for (let i = 0; i <= n; i++) {
      for (let k = 0; k <= i; k++) {
        const c = best[j - 1][k] + cost(k, i, j - 1);
        if (c < best[j][i]) {
          best[j][i] = c;
          from[j][i] = k;
        }
      }
    }
  }
  const groups: Unit[][] = Array.from({ length: g }, () => []);
  let i = n;
  for (let j = g; j >= 1; j--) {
    const k = from[j][i];
    groups[j - 1] = queue.slice(k, i);
    i = k;
  }
  return groups;
}
