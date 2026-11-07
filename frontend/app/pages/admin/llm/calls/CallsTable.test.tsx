/** LLM Calls names nobody: no person column (the server sends no person either). */
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { LlmCall } from "~/api/llmCalls";
import { getAppTheme } from "~/theme";

import CallsTable from "./CallsTable";

function call(overrides: Partial<LlmCall> = {}): LlmCall {
  return {
    id: "row",
    request_id: "7490d3ea-7e73-47e9-9582-79025c893080",
    trace_id: null,
    tenant_id: "default",
    user_id: null,
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

describe("LLM Calls table", () => {
  it("has no person column", () => {
    render(
      <ThemeProvider theme={getAppTheme("light")}>
        <MemoryRouter>
          <CallsTable
            calls={[call()]}
            selectedId={null}
            onSelect={() => {}}
            p95LatencyMs={null}
            sort="newest"
            onSort={() => {}}
            fresh={new Set()}
            now={Date.parse("2026-10-19T10:01:00Z")}
          />
        </MemoryRouter>
      </ThemeProvider>,
    );

    const headers = within(screen.getByRole("table"))
      .getAllByRole("columnheader")
      .map((h) => h.textContent ?? "");
    expect(headers.length).toBeGreaterThan(5);
    expect(headers).not.toContain("User");
    expect(screen.getByText("qwen")).toBeInTheDocument();
  });
});
