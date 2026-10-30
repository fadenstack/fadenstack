/**
 * One call in detail names nobody and points to nobody: no person, no session, conversation, device or
 * project, and no narrowing of the list by any of them -- only by model (M02, M11).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "~/api/llmCalls";
import type { LlmCall, LlmCallDetail } from "~/api/llmCalls";
import { getAppTheme } from "~/theme";

import CallDetail from "./CallDetail";

vi.mock("~/api/llmCalls", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/llmCalls")>();
  return { ...actual, llmCalls: { ...actual.llmCalls, detail: vi.fn() } };
});

const calls = vi.mocked(api.llmCalls);

/** A call as an older server sent it, still with the ids the pages no longer show. */
function call(overrides: Partial<LlmCall> = {}): LlmCall {
  return {
    id: "row-1",
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
    session_id: "sess-0001",
    agent_id: null,
    device_id: "dev-0001",
    conversation_id: "conv-0001",
    turn_id: "turn-0001",
    group_id: null,
    project_id: "proj-0001",
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

function detail(c: LlmCall): LlmCallDetail {
  return {
    call: c,
    tool_calls: [],
    policy_decisions: [],
    related: [],
    related_by: null,
    related_key: null,
    turn_calls: [c],
    client_events: [],
  };
}

function view(c: LlmCall, onFilterModel = vi.fn()) {
  render(
    <ThemeProvider theme={getAppTheme("light")}>
      <CallDetail callId={c.id} preview={c} p95LatencyMs={null} onClose={() => {}} onSelect={() => {}} onFilterModel={onFilterModel} />
    </ThemeProvider>,
  );
  return onFilterModel;
}

beforeEach(() => {
  calls.detail.mockReset();
});

describe("a call in detail", () => {
  it("shows no session, conversation, device or project, and narrows only by model", async () => {
    const c = call();
    calls.detail.mockResolvedValue(detail(c));
    const onFilterModel = view(c);

    await userEvent.click(screen.getByRole("tab", { name: "Details" }));
    const panel = screen.getByTestId("llm-call-detail");
    for (const id of ["sess-0001", "conv-0001", "dev-0001", "proj-0001"]) {
      expect(within(panel).queryByText(id)).toBeNull();
    }
    expect(within(panel).getByText("Hidden")).toBeInTheDocument(); // the person
    expect(within(panel).getByText("turn-0001")).toBeInTheDocument();
    expect(within(panel).queryByText("Agent")).toBeNull(); // none sent

    const filters = within(panel).getAllByRole("button", { name: "Show only calls with this" });
    expect(filters).toHaveLength(1);
    await userEvent.click(filters[0]);
    expect(onFilterModel).toHaveBeenCalledWith("qwen");
  });

  it("names the agent when the server sends it", async () => {
    const c = call({ agent_id: "agent-0001" });
    calls.detail.mockResolvedValue(detail(c));
    view(c);

    await userEvent.click(screen.getByRole("tab", { name: "Details" }));
    expect(screen.getByText("agent-0001")).toBeInTheDocument();
  });
});
