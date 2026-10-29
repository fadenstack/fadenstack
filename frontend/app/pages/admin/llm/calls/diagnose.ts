/**
 * What a call's record says went wrong, or might have: the first thing someone
 * debugging a call wants, before reading any field.
 *
 * Each finding names i18n keys under ``llm_calls.diag``: ``<id>`` for the
 * headline and ``<id>_hint`` for what to check next.
 */

import type { LlmCall, LlmCallPolicy, LlmCallTool, LlmClientEvent } from "~/api/llmCalls";

import { formatDuration, generationSpeed } from "./format";

export type Severity = "error" | "warning" | "info";

export interface Finding {
  id: string;
  severity: Severity;
  params?: Record<string, string | number>;
}

const AUTH_CODES = new Set([
  "invalid_token",
  "invalid_token_sub",
  "missing_authorization",
  "api_token_revoked",
  "api_token_outdated",
  "api_token_expired",
  "sign_in_outdated",
  "sign_in_ended",
  "user_inactive",
  "permission_denied",
  "agent_token_required",
  "agent_token_not_allowed",
]);
const MODEL_CODES = new Set(["model_not_found", "missing_model", "model_required", "model_kind_mismatch", "agent_has_no_model"]);
const SLOW_TTFT_MS = 10_000;
const SLOW_GENERATION_TOKENS_PER_SEC = 5;
const LONG_WAIT_MS = 5 * 60_000;
const APPROVAL_WAIT_MS = 30_000;
const MANY_ROUNDS = 8;

export function isErrorCall(call: Pick<LlmCall, "status_code" | "error_code">): boolean {
  return call.status_code >= 400 || Boolean(call.error_code);
}

function failure(call: LlmCall): Finding | null {
  const code = call.error_code ?? "";
  const status = call.status_code;
  const params = { status, code: code || String(status), wait: formatDuration(call.latency_ms) };
  if (code === "upstream_refused") return { id: "upstream_refused", severity: "error", params };
  if (code === "upstream_authentication_failed") return { id: "provider_key", severity: "error", params };
  if (code === "upstream_request_failed" || code === "upstream_address_refused")
    return { id: "unreachable", severity: "error", params };
  if (code === "no_capacity") return { id: "no_capacity", severity: "error", params };
  if (code === "policy_blocked") return { id: "policy_blocked", severity: "error", params };
  if (code.startsWith("pii_")) return { id: "pii", severity: "error", params };
  if (MODEL_CODES.has(code)) return { id: "unknown_model", severity: "error", params };
  if (AUTH_CODES.has(code) || status === 401 || status === 403) return { id: "auth", severity: "error", params };
  if (code === "budget_exceeded") return { id: "budget_exceeded", severity: "error", params };
  if (status === 429 || code === "resource_limit_reached") return { id: "rate_limited", severity: "error", params };
  if (code === "request_too_large" || status === 413) return { id: "too_large", severity: "error", params };
  if (status >= 500 && call.latency_ms >= LONG_WAIT_MS) return { id: "timeout", severity: "error", params };
  if (status >= 500) return { id: "server_error", severity: "error", params };
  if (status >= 400) return { id: "client_error", severity: "error", params };
  if (code) return { id: "server_error", severity: "error", params };
  return null;
}

export function diagnose(
  call: LlmCall,
  opts: {
    tools?: LlmCallTool[];
    policies?: LlmCallPolicy[];
    p95LatencyMs?: number | null;
    /** What the agent's app reported during the turn: the tools it ran, the approvals it waited for. */
    events?: LlmClientEvent[];
    /** The calls of the turn (the model's rounds for one request). */
    turnCalls?: LlmCall[];
  } = {},
): Finding[] {
  const findings: Finding[] = [];
  const failed = failure(call);
  if (failed) findings.push(failed);

  const events = opts.events ?? [];
  const appFailures = events.filter(
    (e) => e.type === "tool.executed" && e.outcome && e.outcome !== "ok" && e.outcome !== "denied",
  );
  if (appFailures.length) {
    const names = [...new Set(appFailures.map((e) => e.tool ?? "?"))].slice(0, 3).join(", ");
    findings.push({ id: "app_tool_errors", severity: "warning", params: { count: appFailures.length, names } });
  }
  const denied = events.filter(
    (e) => (e.type === "approval.decided" && e.decision === "denied") || (e.type === "tool.executed" && e.outcome === "denied"),
  );
  if (denied.length) {
    const names = [...new Set(denied.map((e) => e.tool ?? "?"))].slice(0, 3).join(", ");
    findings.push({ id: "app_denied", severity: "info", params: { names } });
  }
  const waited = events.reduce((sum, e) => sum + (e.type === "approval.decided" ? (e.wait_ms ?? 0) : 0), 0);
  if (waited >= APPROVAL_WAIT_MS)
    findings.push({ id: "approval_wait", severity: "info", params: { wait: formatDuration(waited) } });
  const rounds = opts.turnCalls?.length ?? 0;
  if (rounds >= MANY_ROUNDS) findings.push({ id: "many_rounds", severity: "warning", params: { count: rounds } });

  const blocked = (opts.policies ?? []).filter((p) => p.outcome === "block" || p.outcome === "rejected");
  if (blocked.length && failed?.id !== "policy_blocked") {
    findings.push({ id: "policy_blocked", severity: "error", params: { code: blocked[0].category } });
  }
  const awaiting = (opts.policies ?? []).filter((p) => p.outcome === "approval_required");
  if (awaiting.length) findings.push({ id: "approval", severity: "info", params: { count: awaiting.length } });

  const failedTools = (opts.tools ?? []).filter((t) => t.is_error);
  if (failedTools.length) {
    const names = [...new Set(failedTools.map((t) => t.tool_name))].slice(0, 3).join(", ");
    findings.push({ id: "tool_errors", severity: "warning", params: { count: failedTools.length, names } });
  }

  if (call.finish_reason === "length")
    findings.push({ id: "truncated", severity: "warning", params: { tokens: call.completion_tokens ?? 0 } });
  if (call.finish_reason === "content_filter") findings.push({ id: "filtered", severity: "warning" });

  if (!isErrorCall(call)) {
    // With tool rounds, the first token of the answer comes after them: not a slow start.
    const toolRounds = (call.mcp_tool_call_count ?? 0) > 0;
    if (!toolRounds && call.ttft_ms !== null && call.ttft_ms >= SLOW_TTFT_MS)
      findings.push({ id: "slow_first_token", severity: "warning", params: { ttft: formatDuration(call.ttft_ms) } });
    const speed = generationSpeed(call.completion_tokens, call.latency_ms, call.ttft_ms);
    if (speed !== null && speed < SLOW_GENERATION_TOKENS_PER_SEC && (call.completion_tokens ?? 0) >= 50)
      findings.push({ id: "slow_generation", severity: "warning", params: { speed: speed.toFixed(1) } });
    if (opts.p95LatencyMs && call.latency_ms > opts.p95LatencyMs * 1.5 && call.latency_ms > 5000)
      findings.push({
        id: "outlier",
        severity: "info",
        params: { latency: formatDuration(call.latency_ms), p95: formatDuration(opts.p95LatencyMs) },
      });
    if (call.total_tokens === null && call.endpoint.includes("completions"))
      findings.push({ id: "no_usage", severity: "info" });
  }

  if ((call.retry_count ?? 0) > 0)
    findings.push({ id: "retried", severity: "info", params: { count: call.retry_count ?? 0 } });

  return findings;
}
