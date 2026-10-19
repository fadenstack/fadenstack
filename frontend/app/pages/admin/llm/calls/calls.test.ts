import { describe, expect, it } from "vitest";

import type { LlmCall } from "~/api/llmCalls";

import { diagnose, isErrorCall } from "./diagnose";
import {
  callName,
  formatCost,
  formatCount,
  formatDuration,
  formatPercent,
  generationSpeed,
  isAgentCall,
  latencyBand,
  shortId,
} from "./format";

function call(overrides: Partial<LlmCall> = {}): LlmCall {
  return {
    id: "row",
    request_id: "7490d3ea-7e73-47e9-9582-79025c893080",
    trace_id: null,
    tenant_id: "default",
    user_id: "u1",
    model_alias: "qwen",
    provider_instance_id: null,
    provider_id: null,
    provider_name: null,
    endpoint: "/v1/chat/completions",
    status_code: 200,
    latency_ms: 2000,
    ttft_ms: null,
    prompt_tokens: 100,
    completion_tokens: 200,
    total_tokens: 300,
    cached_tokens: null,
    error_code: null,
    estimated_total_cost: null,
    currency: "USD",
    stream: false,
    session_id: null,
    agent_id: null,
    device_id: null,
    conversation_id: null,
    turn_id: null,
    group_id: null,
    project_id: null,
    finish_reason: "stop",
    retry_count: 0,
    skills_used: null,
    rag_context: null,
    mcp_tool_call_count: 0,
    mcp_tool_loop_iterations: 0,
    created_at: "2026-10-19T10:00:00Z",
    ...overrides,
  };
}

const ids = (c: LlmCall, opts?: Parameters<typeof diagnose>[1]) => diagnose(c, opts).map((f) => f.id);

describe("format", () => {
  it("makes latencies readable instead of 922804ms", () => {
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(2530)).toBe("2.53 s");
    expect(formatDuration(46645)).toBe("46.6 s");
    expect(formatDuration(922804)).toBe("15m 23s");
    expect(formatDuration(3_900_000)).toBe("1h 05m");
    expect(formatDuration(null)).toBe("—");
  });

  it("shortens counts, costs and percentages", () => {
    expect(formatCount(950)).toBe("950");
    expect(formatCount(1500)).toBe("1.5k");
    expect(formatCount(12_400)).toBe("12.4k");
    expect(formatCount(733_629)).toBe("733.6k");
    expect(formatCount(3_100_000)).toBe("3.1M");
    expect(formatCount(null)).toBe("—");
    expect(formatCost(0.000412)).toBe("$0.000412");
    expect(formatCost(1.2391)).toBe("$1.24");
    expect(formatCost(2, "EUR")).toBe("€2.00");
    expect(formatCost(null)).toBe("—");
    expect(formatPercent(0.0625)).toBe("6.3%");
    expect(formatPercent(0.5)).toBe("50%");
    expect(formatPercent(0)).toBe("0%");
  });

  it("names calls by endpoint and spots agents", () => {
    expect(callName("/v1/chat/completions")).toBe("chat.completions");
    expect(callName("/v1/agents/eplan/chat/completions")).toBe("chat.completions");
    expect(callName("/v1/embeddings")).toBe("embeddings");
    expect(isAgentCall("/v1/agents/eplan/chat/completions")).toBe(true);
    expect(isAgentCall("/v1/chat/completions", "a1")).toBe(true);
    expect(isAgentCall("/v1/chat/completions")).toBe(false);
    expect(shortId("7490d3ea-7e73-47e9-9582-79025c893080")).toBe("7490d3ea");
    expect(shortId("short")).toBe("short");
  });

  it("measures generation speed after the first token", () => {
    expect(generationSpeed(100, 11_000, 1000)).toBe(10);
    expect(generationSpeed(null, 1000, null)).toBeNull();
    expect(generationSpeed(10, 500, 500)).toBeNull();
    expect(generationSpeed(150, 20_514, 20_500)).toBeNull();
  });

  it("bands latency against p95", () => {
    expect(latencyBand(500)).toBe("fast");
    expect(latencyBand(20_000)).toBe("normal");
    expect(latencyBand(90_000)).toBe("slow");
    expect(latencyBand(900_000, 176_000)).toBe("outlier");
  });
});

describe("diagnose", () => {
  it("finds nothing in a healthy call", () => {
    expect(ids(call())).toEqual([]);
  });

  it("calls a long 502 a timeout and a short one a server error", () => {
    expect(ids(call({ status_code: 502, latency_ms: 901_364, total_tokens: null }))).toEqual(["timeout"]);
    expect(ids(call({ status_code: 502, latency_ms: 800, error_code: "server_error" }))).toEqual(["server_error"]);
  });

  it("explains the gateway's own error codes", () => {
    expect(ids(call({ status_code: 502, error_code: "upstream_refused" }))).toEqual(["upstream_refused"]);
    expect(ids(call({ status_code: 503, error_code: "no_capacity" }))).toEqual(["no_capacity"]);
    expect(ids(call({ status_code: 401, error_code: "api_token_expired" }))).toEqual(["auth"]);
    expect(ids(call({ status_code: 404, error_code: "model_not_found" }))).toEqual(["unknown_model"]);
    expect(ids(call({ status_code: 503, error_code: "pii_scan_incomplete" }))).toEqual(["pii"]);
    expect(ids(call({ status_code: 429 }))).toEqual(["rate_limited"]);
    expect(ids(call({ status_code: 400, error_code: "validation_error" }))).toEqual(["client_error"]);
    expect(isErrorCall(call({ status_code: 200, error_code: "upstream_refused" }))).toBe(true);
  });

  it("warns about truncation, slow tokens, failed tools and blocks", () => {
    expect(ids(call({ finish_reason: "length" }))).toEqual(["truncated"]);
    expect(ids(call({ ttft_ms: 15_000, latency_ms: 20_000 }))).toContain("slow_first_token");
    expect(ids(call({ ttft_ms: 20_500, latency_ms: 20_514, mcp_tool_call_count: 5 }))).not.toContain("slow_first_token");
    expect(ids(call({ completion_tokens: 100, latency_ms: 60_000 }))).toContain("slow_generation");
    const tools = [
      { id: "1", iteration: 0, tool_name: "search", mcp_server: null, latency_ms: 10, is_error: true, error_kind: "timeout", created_at: "" },
    ];
    expect(diagnose(call(), { tools })[0]).toMatchObject({ id: "tool_errors", params: { count: 1, names: "search" } });
    const policies = [{ stage: "input", outcome: "block", category: "pii", check_name: "x", tool_id: null, created_at: "" }];
    expect(ids(call({ status_code: 403 }), { policies })).toEqual(["auth", "policy_blocked"]);
  });

  it("flags an outlier against p95, and missing usage", () => {
    expect(ids(call({ latency_ms: 300_000, completion_tokens: 5000 }), { p95LatencyMs: 100_000 })).toContain("outlier");
    expect(ids(call({ total_tokens: null, prompt_tokens: null, completion_tokens: null }))).toEqual(["no_usage"]);
    expect(ids(call({ retry_count: 2 }))).toEqual(["retried"]);
  });
});
