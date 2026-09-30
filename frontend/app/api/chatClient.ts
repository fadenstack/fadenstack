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
        return new Error(parsedMessage);
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

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.startsWith("data:")) continue;

      const data = trimmed.slice(5).trim();
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
          failure = new Error(
            apiMessage ||
              parsed.error.message ||
              parsed.error.type ||
              "Unknown streaming error",
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
 */
export function streamChat(payload: Record<string, unknown>): StreamHandle {
  return postStream("/completions", { ...payload, stream: true });
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
