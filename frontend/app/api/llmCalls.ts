/**
 * LLM Calls explorer API: gateway calls, their summary, one call in detail, and routing.
 *
 * Metadata only: the gateway does not keep prompts or completions, and a
 * failed tool's text is sealed. What a call was, how long it took, what it
 * cost and how it ended is all here -- but not whose it was: no person, and no
 * session, conversation, device or project id (each points to one person, and
 * calls are not found by them either). Older servers may still send those ids;
 * the pages do not show them.
 */

import { clearCachedAccess } from "~/lib/adminConstants";

const BASE = "/api/llm/graph";

export interface LlmCall {
  id: string;
  request_id: string;
  trace_id: string | null;
  tenant_id: string;
  /** Null: calls name nobody (who sent one is not shown on these pages). */
  user_id: string | null;
  model_alias: string | null;
  provider_instance_id: string | null;
  provider_id: string | null;
  provider_name: string | null;
  endpoint: string;
  status_code: number;
  latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  cached_tokens: number | null;
  error_code: string | null;
  estimated_total_cost: number | null;
  currency: string | null;
  stream: boolean | null;
  /** Not sent (see the module's note); not shown. */
  session_id?: string | null;
  /** Shown when the server sends it. */
  agent_id?: string | null;
  /** Not sent; not shown. */
  device_id?: string | null;
  /** Not sent; not shown. */
  conversation_id?: string | null;
  turn_id: string | null;
  group_id: string | null;
  /** Not sent; not shown. */
  project_id?: string | null;
  finish_reason: string | null;
  retry_count: number | null;
  skills_used: Array<{ name?: string; slug?: string; version?: string }> | null;
  rag_context: { chunk_count?: number; top_k?: number; collection_ids?: string[] } | null;
  mcp_tool_call_count: number | null;
  mcp_tool_loop_iterations: number | null;
  created_at: string;
}

export interface LlmCallPage {
  items: LlmCall[];
  total: number;
  page: number;
  limit: number;
}

export interface LlmCallStats {
  total: number;
  errors: number;
  error_rate: number | null;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  p95_ttft_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_total_cost: number | null;
  models: Array<{ model_alias: string; calls: number }>;
}

export interface LlmCallTool {
  id: string;
  iteration: number;
  tool_name: string;
  mcp_server: string | null;
  latency_ms: number | null;
  is_error: boolean;
  error_kind: string | null;
  created_at: string;
}

export interface LlmCallPolicy {
  stage: string;
  outcome: string;
  category: string;
  check_name: string;
  tool_id: string | null;
  created_at: string;
}

export interface LlmClientEvent {
  type: "tool.executed" | "approval.decided" | string;
  at: string;
  tool: string | null;
  source: string | null;
  server: string | null;
  outcome: string | null;
  duration_ms: number | null;
  decision: string | null;
  wait_ms: number | null;
  scope: string | null;
}

export interface LlmCallDetail {
  call: LlmCall;
  tool_calls: LlmCallTool[];
  policy_decisions: LlmCallPolicy[];
  related: LlmCall[];
  related_by: "conversation_id" | "session_id" | "trace_id" | null;
  related_key: string | null;
  /** The calls of the same turn (one user request), oldest first. */
  turn_calls: LlmCall[];
  /** The tools the agent's app ran and the approvals it waited for during the turn. */
  client_events: LlmClientEvent[];
}

/** One user request: the rounds an agent's app made for it, or a call on its own. */
export interface LlmTurn {
  key: string;
  turn_id: string | null;
  /** Not sent (see the module's note); not shown. */
  conversation_id?: string | null;
  /** Not sent; not shown. */
  session_id?: string | null;
  /** Shown (as an agent's call) when the server sends it. */
  agent_id?: string | null;
  /** Not sent; not shown. */
  device_id?: string | null;
  tenant_id: string;
  /** Null: turns name nobody. */
  user_id: string | null;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  calls: number;
  tool_rounds: number;
  errors: number;
  llm_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  estimated_total_cost: number | null;
  currency: string | null;
  server_tools: number;
  client_tools: number;
  client_tool_errors: number;
  approvals: number;
  approvals_denied: number;
  approval_wait_ms: number;
  last_call_id: string;
  model_alias: string | null;
  provider_instance_id: string | null;
  provider_id: string | null;
  provider_name: string | null;
  endpoint: string;
  finish_reason: string | null;
  status_code: number;
  worst_status: number;
  error_code: string | null;
}

export interface LlmTurnPage {
  items: LlmTurn[];
  total: number;
  page: number;
  limit: number;
}

export interface LlmRoute {
  model_alias: string | null;
  provider_instance_id: string | null;
  provider_id: string | null;
  provider_name: string | null;
  calls: number;
  errors: number;
  p95_latency_ms: number | null;
  tokens: number;
  last_call_at: string | null;
}

export type CallOutcome = "ok" | "error";
export type CallSort = "newest" | "oldest" | "slowest" | "tokens";

/** What the list narrows to. No agent, session, conversation or device: those would single out a person. */
export interface CallQuery {
  start: string;
  end: string;
  q?: string;
  model_alias?: string;
  outcome?: CallOutcome;
  min_latency_ms?: number;
}

function qs(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    signal,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) clearCachedAccess();
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const llmCalls = {
  list(query: CallQuery, opts: { page?: number; limit?: number; sort?: CallSort } = {}, signal?: AbortSignal) {
    return request<LlmCallPage>(`/calls${qs({ ...query, ...opts })}`, signal);
  },
  turns(query: CallQuery, opts: { page?: number; limit?: number; sort?: CallSort } = {}, signal?: AbortSignal) {
    return request<LlmTurnPage>(`/turns${qs({ ...query, ...opts })}`, signal);
  },
  stats(query: CallQuery, signal?: AbortSignal) {
    return request<LlmCallStats>(`/calls/stats${qs(query)}`, signal);
  },
  /** By the call's log row id (`LlmCall.id`), not its request id: callers choose those, and may repeat them. */
  detail(callId: string, signal?: AbortSignal) {
    return request<LlmCallDetail>(`/calls/${encodeURIComponent(callId)}`, signal);
  },
  routing(query: CallQuery, signal?: AbortSignal) {
    return request<{ routes: LlmRoute[] }>(`/routing${qs(query)}`, signal);
  },
};
