/**
 * User-facing Chat API client.
 *
 * All requests go through the backend proxy at `/api/chat/*`, which
 * converts the httponly `fapiauth` cookie into a Bearer token and
 * forwards to the API gateway.
 */

import type {
  ChatProject,
  ChatSession,
  ChatMessage,
  ChatAttachment,
  ModelAlias,
  Capacity,
  TokenUsage,
  StreamDelta,
  FadenEvent,
} from "./chatTypes";

export type {
  ChatProject,
  ChatSession,
  ChatMessage,
  ChatAttachment,
  ModelAlias,
  Capacity,
  TokenUsage,
  StreamDelta,
};

const CONFIGURED_BASE = (
  import.meta.env.VITE_CHAT_API_BASE as string | undefined
)?.replace(/\/$/, "");
const CONFIGURED_API_BASE = (
  import.meta.env.VITE_API_BASE as string | undefined
)?.replace(/\/$/, "");

const BASE =
  CONFIGURED_BASE ??
  (CONFIGURED_API_BASE ? `${CONFIGURED_API_BASE}/chat` : "/api/chat");

// ── Helpers ──────────────────────────────────────────────────────

/** What the gateway said about a refused prompt (``faden.governance``). */
export interface GovernanceNotice {
  outcome: string;
  stage: string;
  category?: string;
  message: string;
  decision_id?: string | null;
  message_id?: string | null;
}

/**
 * The gateway's refusal when a request would go over a budget that applies (429, `insufficient_quota`,
 * `error.param` the kind of budget: `personal`, `team`, `agent`, `organisation`). It starts again with the
 * budget's next period, not in a moment, so the chat words it apart from a rate limit, in the reader's
 * language from the code and the kind (the gateway's own message is English).
 */
export const BUDGET_EXCEEDED = "budget_exceeded";

/** An error from the chat API, with what the page needs to show it on its turn. */
export class ChatApiError extends Error {
  code: string | null;
  type: string | null;
  status: number | null;
  /** What the code is about (`error.param`), e.g. the kind of budget that refused. */
  param: string | null;
  /** Set when a governance check refused the prompt. */
  governance: GovernanceNotice | null;
  /** Seconds to wait, for a rate limit. */
  retryAfter: number | null;

  constructor(
    message: string,
    init: {
      code?: string | null;
      type?: string | null;
      status?: number | null;
      param?: string | null;
      governance?: GovernanceNotice | null;
      retryAfter?: number | null;
    } = {},
  ) {
    super(message);
    this.name = "ChatApiError";
    this.code = init.code ?? null;
    this.type = init.type ?? null;
    this.status = init.status ?? null;
    this.param = init.param ?? null;
    this.governance = init.governance ?? null;
    this.retryAfter = init.retryAfter ?? null;
  }
}

function errorFields(payload: unknown): {
  code: string | null;
  type: string | null;
  status: number | null;
  param: string | null;
  governance: GovernanceNotice | null;
} {
  const record = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
  const error = (record.error && typeof record.error === "object" ? record.error : {}) as Record<
    string,
    unknown
  >;
  const faden = (record.faden && typeof record.faden === "object" ? record.faden : {}) as Record<
    string,
    unknown
  >;
  const governance =
    faden.governance && typeof faden.governance === "object"
      ? (faden.governance as GovernanceNotice)
      : null;
  return {
    code: typeof error.code === "string" ? error.code : null,
    type: typeof error.type === "string" ? error.type : null,
    status: typeof error.status === "number" ? error.status : null,
    param: typeof error.param === "string" ? error.param : null,
    governance,
  };
}

function parseApiErrorMessage(payload: unknown, status: number): string | null {
  if (!payload || typeof payload !== "object") return null;

  const record = payload as Record<string, unknown>;
  const detail =
    record.detail && typeof record.detail === "object"
      ? (record.detail as Record<string, unknown>)
      : null;
  const error =
    detail?.error && typeof detail.error === "object"
      ? (detail.error as Record<string, unknown>)
      : record.error && typeof record.error === "object"
        ? (record.error as Record<string, unknown>)
        : null;

  const code =
    typeof error?.code === "string"
      ? error.code
      : typeof detail?.code === "string"
        ? detail.code
        : null;
  const type =
    typeof error?.type === "string"
      ? error.type
      : typeof detail?.type === "string"
        ? detail.type
        : null;
  const message =
    typeof error?.message === "string"
      ? error.message
      : typeof detail?.message === "string"
        ? detail.message
        : typeof record.message === "string"
          ? record.message
          : null;

  // A used-up budget is no rate limit: the chat words it from the code and the kind of budget.
  if (code === BUDGET_EXCEEDED) return message ?? code;

  if (
    status === 429 ||
    code === "rate_limit_error" ||
    code === "rate_limit_rpm" ||
    code === "rate_limit_tpm" ||
    type === "rate_limit_error"
  ) {
    return message
      ? `Rate limit reached (429): ${message}`
      : "Rate limit reached (429). Please wait and retry, or choose another model/provider.";
  }

  return message;
}

async function buildApiError(res: Response): Promise<Error> {
  let text = res.statusText;
  try {
    const raw = await res.text();
    text = raw || res.statusText;
    try {
      const parsed = JSON.parse(raw) as unknown;
      const parsedMessage = parseApiErrorMessage(parsed, res.status);
      if (parsedMessage) {
        const retry = Number(res.headers.get("retry-after"));
        return new ChatApiError(parsedMessage, {
          ...errorFields(parsed),
          status: res.status,
          retryAfter: Number.isFinite(retry) && retry > 0 ? retry : null,
        });
      }
    } catch {
      // keep raw text fallback
    }
  } catch {
    // keep statusText fallback
  }
  return new Error(`API ${res.status}: ${text}`);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      Pragma: "no-cache",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });

  if (!res.ok) {
    throw await buildApiError(res);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Streaming SSE ────────────────────────────────────────────────

export interface StreamHandle {
  /** Async iterator of parsed deltas. */
  reader: AsyncGenerator<StreamDelta>;
  /** Call to abort the stream. */
  abort: () => void;
}

/**
 * A streamed reply's events as deltas. The ids the gateway sends as headers
 * (the trace, the reply's turn) ride on the first delta.
 *
 * An error event is thrown -- once the stream has ended, not when it comes:
 * the gateway stores the failed reply before it closes the stream, so a page
 * that reloads the chat then finds it. With ``skipErrors`` it is dropped.
 */
async function* deltasOf(
  res: Response,
  { skipErrors = false }: { skipErrors?: boolean } = {},
): AsyncGenerator<StreamDelta> {
  const ids: StreamDelta = {};
  const traceId = res.headers.get("x-langfuse-trace-id");
  const turnId = res.headers.get("x-faden-turn-id");
  if (traceId) ids.trace_id = traceId;
  if (turnId) ids.turn_id = turnId;
  let idsSent = false;
  let failure: Error | null = null;

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");
  const decoder = new TextDecoder();
  let buffer = "";
  // The name of the SSE event the next data line belongs to (``faden``: the
  // gateway's own events, beside the OpenAI chunks).
  let eventName: string | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) {
        eventName = null;
        continue;
      }
      if (trimmed.startsWith("event:")) {
        eventName = trimmed.slice(6).trim();
        continue;
      }
      if (!trimmed.startsWith("data:")) continue;

      const data = trimmed.slice(5).trim();
      if (eventName === "faden") {
        eventName = null;
        try {
          yield { faden: JSON.parse(data) as FadenEvent };
        } catch {
          // A malformed event is skipped, as a malformed chunk is.
        }
        continue;
      }
      if (data === "[DONE]") {
        if (failure) throw failure;
        return;
      }

      let parsed: Record<string, any>;
      try {
        parsed = JSON.parse(data);
      } catch {
        continue; // malformed
      }

      // Detect error payloads from the gateway
      if (parsed.error) {
        if (!skipErrors && !failure) {
          const apiMessage = parseApiErrorMessage(parsed, 500);
          failure = new ChatApiError(
            apiMessage ||
              parsed.error.message ||
              parsed.error.type ||
              "Unknown streaming error",
            errorFields(parsed),
          );
        }
        continue;
      }

      const choice = parsed.choices?.[0];
      const delta: StreamDelta = {};

      if (choice?.delta?.content) {
        delta.content = choice.delta.content;
      }
      // The gateway sends it as reasoning_content; a vLLM streamed
      // straight through may call it reasoning.
      const thinking = choice?.delta?.reasoning_content ?? choice?.delta?.reasoning;
      if (typeof thinking === "string" && thinking) {
        delta.reasoning = thinking;
      }
      if (choice?.finish_reason) {
        delta.finish_reason = choice.finish_reason;
      }
      if (parsed.usage) {
        delta.usage = {
          prompt_tokens: parsed.usage.prompt_tokens ?? 0,
          completion_tokens: parsed.usage.completion_tokens ?? 0,
          total_tokens: parsed.usage.total_tokens ?? 0,
        };
      }
      if (!idsSent) {
        Object.assign(delta, ids);
        idsSent = true;
      }

      if (
        delta.content ||
        delta.reasoning ||
        delta.finish_reason ||
        delta.usage ||
        delta.trace_id ||
        delta.turn_id
      ) {
        yield delta;
      }
    }
  }
  if (failure) throw failure;
  // A stream that failed before any event still names its reply.
  if (!idsSent && (ids.trace_id || ids.turn_id)) yield ids;
}

/** A streamed POST, as deltas; an error response is thrown. */
function postStream(path: string, payload: Record<string, unknown>): StreamHandle {
  const controller = new AbortController();

  async function* read(): AsyncGenerator<StreamDelta> {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!res.ok) {
      throw await buildApiError(res);
    }
    yield* deltasOf(res);
  }

  return { reader: read(), abort: () => controller.abort() };
}

/**
 * Start a streaming chat completion request.
 *
 * Returns an async generator of `StreamDelta` objects and an `abort()`
 * function to cancel. Usage from the final `[DONE]` chunk is extracted
 * automatically.
 *
 * The reply runs on its own (``background``): closing the stream does not
 * stop it -- the page may leave and come back. ``chatApi.stopTurn`` stops it.
 */
export function streamChat(payload: Record<string, unknown>): StreamHandle {
  return postStream("/completions", { ...payload, stream: true, background: true });
}

/**
 * Try the chat's last failed or stopped reply again: a new reply replaces it,
 * streamed like a completion. ``model`` defaults to the failed reply's.
 */
export function retryTurn(
  sessionId: string,
  turnId: string,
  body: { model?: string; skills?: string[] } = {},
): StreamHandle {
  return postStream(`/sessions/${sessionId}/turns/${turnId}/retry`, body);
}

/**
 * Reconnect to an in-progress SSE stream for a session.
 *
 * Returns the same `StreamHandle` interface as `streamChat` so callers
 * can use it interchangeably. No stream (204), or any failure, reads as
 * an empty one.
 */
export function resumeStream(sessionId: string): StreamHandle {
  const controller = new AbortController();

  async function* read(): AsyncGenerator<StreamDelta> {
    const res = await fetch(`${BASE}/sessions/${sessionId}/stream`, {
      credentials: "include",
      signal: controller.signal,
      headers: { "Cache-Control": "no-store" },
    });

    // 204 = no buffer available
    if (res.status === 204 || !res.ok) return;
    yield* deltasOf(res, { skipErrors: true });
  }

  return { reader: read(), abort: () => controller.abort() };
}

// ── REST API ─────────────────────────────────────────────────────

export const chatApi = {
  /** Approve or decline a tool call waiting in this chat (``faden.approval_required``). */
  answerApproval: (approvalId: string, approved: boolean) =>
    request<{ approval_id: string; approved: boolean }>(
      `/approvals/${encodeURIComponent(approvalId)}`,
      { method: "POST", body: JSON.stringify({ approved }) },
    ),

  // Models
  listModels: async () => {
    const payload = await request<
      { data?: ModelAlias[]; models?: ModelAlias[] } | ModelAlias[]
    >("/models");

    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload.data)) return payload.data;
    if (Array.isArray(payload.models)) return payload.models;
    return [];
  },

  // Capacity
  getCapacity: () => request<Capacity>("/capacity"),

  // Projects
  listProjects: () =>
    request<{ data: ChatProject[] }>("/projects").then((r) => r.data),

  createProject: (body: {
    name: string;
    description?: string;
    system_instructions?: string;
    model_alias?: string;
  }) =>
    request<ChatProject>("/projects", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getProject: (id: string) => request<ChatProject>(`/projects/${id}`),

  updateProject: (
    id: string,
    body: Partial<{
      name: string;
      description: string;
      system_instructions: string;
      model_alias: string;
    }>,
  ) =>
    request<ChatProject>(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, { method: "DELETE" }),

  // Sessions
  listSessions: (projectId?: string) => {
    const qs = projectId ? `?project_id=${projectId}` : "";
    return request<{ data: ChatSession[] }>(`/sessions${qs}`).then(
      (r) => r.data,
    );
  },

  createSession: (body: { project_id?: string; title?: string }) =>
    request<ChatSession>("/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getSession: (id: string) => request<ChatSession>(`/sessions/${id}`),

  updateSession: (
    id: string,
    body: Partial<{ title: string; status: string; project_id: string | null }>,
  ) =>
    request<ChatSession>(`/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteSession: (id: string) =>
    request<void>(`/sessions/${id}`, { method: "DELETE" }),

  // Messages
  listMessages: (sessionId: string, limit = 100) =>
    request<{ data: ChatMessage[] }>(
      `/sessions/${sessionId}/messages?limit=${limit}`,
    ).then((r) => r.data),

  // Stream reconnection
  streamStatus: (sessionId: string) =>
    request<{ active: boolean }>(`/sessions/${sessionId}/stream/status`),

  /** Stop a reply being written; what was written is kept. */
  stopTurn: (sessionId: string, turnId: string) =>
    request<{ stopping: boolean }>(`/sessions/${sessionId}/turns/${turnId}/stop`, { method: "POST" }),

  // Attachments
  uploadAttachment: async (sessionId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/sessions/${sessionId}/attachments`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!res.ok) {
      throw await buildApiError(res);
    }
    return res.json() as Promise<ChatAttachment>;
  },

  listAttachments: (sessionId: string) =>
    request<{ data: ChatAttachment[] }>(
      `/sessions/${sessionId}/attachments`,
    ).then((r) => r.data),

  deleteAttachment: (id: string) =>
    request<void>(`/attachments/${id}`, { method: "DELETE" }),

  // Non-streaming chat
  chatCompletion: (payload: Record<string, unknown>) =>
    request<Record<string, unknown>>("/completions", {
      method: "POST",
      body: JSON.stringify({ ...payload, stream: false }),
    }),
};
