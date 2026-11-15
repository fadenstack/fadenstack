import { describe, expect, it } from "vitest";

import type { ClientEvent } from "~/api/agents";

import { EVENT_TYPES, summarizeEvent } from "./agentEvents";

const event = (type: string, data: Record<string, unknown>, conversation_id: string | null = null): ClientEvent => ({
  id: "e1",
  type,
  user_id: "u1",
  device_id: "d1",
  conversation_id,
  data,
  occurred_at: null,
  created_at: "2026-10-01T10:00:00Z",
});

describe("summarizeEvent", () => {
  it("a tool run: the tool, where it ran, how long and how it ended", () => {
    expect(
      summarizeEvent(event("tool.executed", { tool: "read_page", source: "mcp", server: "eplan-db", duration_ms: 42, outcome: "ok" })),
    ).toEqual({ subject: "read_page", result: "ok", tone: "success", durationMs: 42, facts: ["mcp", "eplan-db"] });
  });

  it("an approval no one was asked about: what let it through", () => {
    expect(summarizeEvent(event("approval.decided", { tool: "delete_page", decision: "approved", scope: "bypass" }))).toMatchObject({
      subject: "delete_page",
      result: "approved",
      scope: "bypass",
      durationMs: undefined,
    });
  });

  it("an approval: the decision and the wait", () => {
    expect(summarizeEvent(event("approval.decided", { tool: "delete_page", decision: "denied", wait_ms: 3000 }))).toMatchObject({
      subject: "delete_page",
      result: "denied",
      tone: "warning",
      durationMs: 3000,
    });
  });

  it("a local MCP server: its label and how many tools", () => {
    expect(summarizeEvent(event("local_mcp.attached", { label: "files", command: "mcp-files", tools: ["a", "b"] }))).toMatchObject({
      subject: "files",
      facts: ["mcp-files"],
      count: { key: "tools", value: 2 },
    });
  });

  it("plain http on the local network: the server's origin, switched on (a warning) or off", () => {
    expect(summarizeEvent(event("local_mcp.plain_http", { source: "http://192.168.1.20:8080", state: "on" }))).toEqual({
      subject: "http://192.168.1.20:8080",
      result: "on",
      tone: "warning",
      facts: [],
    });
    expect(summarizeEvent(event("local_mcp.plain_http", { source: "http://nas.local", state: "off" }))).toMatchObject({
      subject: "http://nas.local",
      result: "off",
      tone: "default",
    });
    expect(EVENT_TYPES).toContain("local_mcp.plain_http"); // the Activity tab's type filter offers it
  });

  it("session counts, and anything unknown without failing", () => {
    expect(summarizeEvent(event("session.stats", { turns: 7 }, "c-9"))).toMatchObject({ subject: "c-9", count: { key: "turns", value: 7 } });
    expect(summarizeEvent(event("something.new", { x: 1 }))).toEqual({ subject: "", tone: "default", facts: [] });
  });
});
