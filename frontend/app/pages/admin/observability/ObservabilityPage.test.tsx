/**
 * Observability without a ranking of people (M11): no Top users, even from
 * a server that still sends them; a request's person is "hidden" unless the
 * server names it (to a usage auditor, person-level reporting on).
 */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "~/api/observability";
import type { RequestLog, Summary } from "~/api/observability";
import { getAppTheme } from "~/theme";
import { renderPage } from "~/test/renderPage";

import ObservabilityPage from "./ObservabilityPage";

vi.mock("~/api/observability", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/observability")>();
  return {
    ...actual,
    observability: {
      ...actual.observability,
      summary: vi.fn(),
      timeseries: vi.fn(),
      performance: vi.fn(),
      requests: vi.fn(),
    },
  };
});

vi.mock("~/components/dashboard/TimeSeriesChart", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} />,
}));

const obs = vi.mocked(api.observability);

function view() {
  return renderPage(
    <ThemeProvider theme={getAppTheme("light")}>
      <ObservabilityPage />
    </ThemeProvider>,
    { path: "/admin/observability" },
  );
}

function summary(): Summary {
  return {
    total_requests: 40,
    total_prompt_tokens: 1000,
    total_completion_tokens: 500,
    total_tokens: 1500,
    estimated_total_cost: 0.5,
    error_count: 1,
    avg_latency_ms: 120,
    by_provider: [],
    by_model: [{ model_alias: "qwen3.8-27b-fp8", total_requests: 40, total_tokens: 1500, estimated_total_cost: 0.5 }],
  };
}

function request(overrides: Partial<RequestLog> = {}): RequestLog {
  return {
    id: "r-1",
    request_id: "req-1",
    trace_id: null,
    tenant_id: "default",
    user_id: null,
    model_alias: "qwen3.8-27b-fp8",
    provider_instance_id: null,
    endpoint: "/v1/chat/completions",
    status_code: 200,
    latency_ms: 120,
    ttft_ms: null,
    prompt_tokens: 10,
    completion_tokens: 5,
    total_tokens: 15,
    error_code: null,
    estimated_input_cost: null,
    estimated_output_cost: null,
    estimated_total_cost: null,
    currency: null,
    cost_estimate_status: null,
    cached_tokens: null,
    stream: false,
    session_id: null,
    finish_reason: "stop",
    retry_count: 0,
    skills_used: null,
    rag_context: null,
    mcp_tool_call_count: 0,
    mcp_tool_loop_iterations: 0,
    created_at: "2026-10-28T08:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  // An older server still sends a ranking; the page shows none of it.
  obs.summary.mockResolvedValue({
    ...summary(),
    top_users: [{ user_id: "u-heavy", total_requests: 39, total_tokens: 1400, estimated_total_cost: 0.49 }],
  } as Summary);
  obs.timeseries.mockResolvedValue([]);
  obs.performance.mockResolvedValue({
    p50_latency_ms: 100,
    p95_latency_ms: 200,
    p99_latency_ms: 300,
    p50_ttft_ms: null,
    p95_ttft_ms: null,
    p99_ttft_ms: null,
    avg_latency_ms: 120,
    total_requests: 40,
    error_count: 1,
    error_rate: 0.025,
  });
  obs.requests.mockReset();
});

describe("Observability overview", () => {
  it("ranks no people", async () => {
    view();

    expect(await screen.findByRole("region", { name: "Spend by Model" })).toBeInTheDocument();
    expect(await screen.findByText("qwen3.8-27b-fp8")).toBeInTheDocument();
    expect(screen.queryByText(/top users/i)).toBeNull();
    expect(screen.queryByText("u-heavy")).toBeNull();
  });
});

describe("Observability requests", () => {
  it("has no person column and no search by person; a request's details say its person is hidden", async () => {
    obs.requests.mockResolvedValue({ items: [request()], total: 1, page: 1, limit: 50 });
    const { container } = view();
    await userEvent.click(await screen.findByRole("tab", { name: "Requests" }));

    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).not.toContain("User");
    expect(screen.getAllByRole("textbox").map((box) => box.getAttribute("id"))).toHaveLength(1); // the model filter only
    expect(screen.getByRole("textbox", { name: "Model" })).toBeInTheDocument();
    expect(container.querySelector("[data-user-hidden]")).toBeNull();

    await userEvent.click(within(table).getByText("qwen3.8-27b-fp8"));
    expect(await screen.findByText("Hidden")).toBeInTheDocument();
    expect(container.querySelector("[data-user-hidden]")).not.toBeNull();
    expect(obs.requests).toHaveBeenCalledWith(expect.any(String), expect.any(String), {
      page: 1,
      limit: 50,
      model_alias: undefined,
    });
  });
});
