import { describe, expect, it } from "vitest";

import type { LlmCall, LlmClientEvent } from "~/api/llmCalls";

import { isBatched, placeEvents } from "./timeline";

/** A round ending at `end` (HH:MM:SS.mmm on 19 Oct) after `latency` ms. */
function round(end: string, latency: number): LlmCall {
  return { id: end, created_at: `2026-10-19T${end}Z`, latency_ms: latency } as LlmCall;
}

function ev(type: string, tool: string, ms: number, at = "2026-10-19T13:40:20.928Z"): LlmClientEvent {
  return {
    type,
    at,
    tool,
    source: "host",
    server: null,
    outcome: type === "tool.executed" ? "ok" : null,
    duration_ms: type === "tool.executed" ? ms : null,
    decision: type === "approval.decided" ? "approved" : null,
    wait_ms: type === "approval.decided" ? ms : null,
    scope: null,
  };
}

const at = (ms: number) => new Date(ms).toISOString().slice(11, 23);

describe("placeEvents", () => {
  // The Word add-in's request on .22 (19 Oct): 9 rounds, its 13 events sent in one batch.
  const calls = [
    round("13:36:19.096", 10_128),
    round("13:36:38.535", 16_287),
    round("13:36:47.413", 6_760),
    round("13:36:51.426", 3_144),
    round("13:38:45.691", 114_242),
    round("13:38:49.976", 3_265),
    round("13:39:37.812", 47_820),
    round("13:39:43.729", 4_690),
    round("13:40:21.992", 38_211),
  ];
  const events = [
    ev("approval.decided", "search_web", 1079),
    ev("tool.executed", "search_web", 2054),
    ev("approval.decided", "search_web", 974),
    ev("tool.executed", "search_web", 1108),
    ev("approval.decided", "open_page", 351),
    ev("tool.executed", "open_page", 498),
    ev("tool.executed", "read_context", 5),
    ev("approval.decided", "open_page", 548),
    ev("tool.executed", "open_page", 445),
    ev("tool.executed", "read_context", 2),
    ev("approval.decided", "open_page", 286),
    ev("tool.executed", "open_page", 897),
    ev("tool.executed", "read_context", 4),
  ];

  it("lays a batch into the gaps between the rounds, in order", () => {
    const placed = placeEvents(calls, events);
    expect(placed.inferred).toBe(true);
    const starts = placed.events.map((p) => `${p.event.tool}@${at(p.start)}`);
    expect(starts).toEqual([
      "search_web@13:36:19.096", // after round 1, approval first
      "search_web@13:36:20.175",
      "search_web@13:36:38.535", // after round 2
      "search_web@13:36:39.509",
      "open_page@13:36:47.413", // after round 3
      "open_page@13:36:47.764",
      "read_context@13:36:51.426", // the 23 ms gap after round 4
      "open_page@13:38:45.691",
      "open_page@13:38:46.239",
      "read_context@13:38:49.976",
      "open_page@13:39:37.812",
      "open_page@13:39:38.098",
      "read_context@13:39:43.729",
    ]);
  });

  it("keeps the app's own times when they are distinct", () => {
    const own = [ev("tool.executed", "a", 100, "2026-10-19T13:36:20.000Z"), ev("approval.decided", "b", 500, "2026-10-19T13:36:40.000Z")];
    expect(isBatched(own)).toBe(false);
    const placed = placeEvents(calls, own);
    expect(placed.inferred).toBe(false);
    expect(placed.events.map((p) => at(p.start))).toEqual(["13:36:20.000", "13:36:39.500"]);
  });

  it("puts several runs into one gap when the model asked for them together", () => {
    const placed = placeEvents([round("13:36:19.096", 1000), round("13:36:24.000", 1000)], events.slice(0, 4));
    expect(placed.events.map((p) => at(p.start))).toEqual(["13:36:19.096", "13:36:20.175", "13:36:22.229", "13:36:23.203"]);
  });

  it("uses only the gaps after rounds that asked for tools", () => {
    const asked = { ...round("13:36:19.096", 1000), finish_reason: "tool_calls" } as LlmCall;
    const stopped = { ...round("13:36:30.000", 1000), finish_reason: "stop" } as LlmCall;
    const last = round("13:36:40.000", 1000);
    const batch = [ev("tool.executed", "a", 5), ev("tool.executed", "b", 5)];
    const placed = placeEvents([asked, stopped, last], batch);
    expect(placed.events.map((p) => at(p.start))).toEqual(["13:36:19.096", "13:36:19.101"]);
  });
});
