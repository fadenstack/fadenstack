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

const BASE = "/api/chat";

// ── Helpers ──────────────────────────────────────────────────────

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
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
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
 * Start a streaming chat completion request.
 *
 * Returns an async generator of `StreamDelta` objects and an `abort()`
 * function to cancel. Usage from the final `[DONE]` chunk is extracted
 * automatically.
 */
export function streamChat(payload: Record<string, unknown>): StreamHandle {
  const controller = new AbortController();

  async function* read(): AsyncGenerator<StreamDelta> {
    const res = await fetch(`${BASE}/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ ...payload, stream: true }),
      signal: controller.signal,
    });

    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`API ${res.status}: ${text}`);
    }

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
        if (data === "[DONE]") return;

        try {
          const parsed = JSON.parse(data);

          // Detect error payloads from the gateway
          if (parsed.error) {
            const msg =
              parsed.error.message ||
              parsed.error.type ||
              "Unknown streaming error";
            throw new Error(msg);
          }

          const choice = parsed.choices?.[0];
          const delta: StreamDelta = {};

          if (choice?.delta?.content) {
            delta.content = choice.delta.content;
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

          if (delta.content || delta.finish_reason || delta.usage) {
            yield delta;
          }
        } catch (e) {
          // Re-throw gateway errors, skip malformed JSON
          if (
            e instanceof Error &&
            e.message !== "Unexpected end of JSON input"
          ) {
            throw e;
          }
        }
      }
    }
  }

  return { reader: read(), abort: () => controller.abort() };
}

/**
 * Reconnect to an in-progress SSE stream for a session.
 *
 * Returns the same `StreamHandle` interface as `streamChat` so callers
 * can use it interchangeably.  Returns `null` if no active stream.
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
    if (res.status === 204) return;
    if (!res.ok) return;

    const reader = res.body?.getReader();
    if (!reader) return;

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
        if (data === "[DONE]") return;

        try {
          const parsed = JSON.parse(data);
          if (parsed.error) continue;

          const choice = parsed.choices?.[0];
          const delta: StreamDelta = {};

          if (choice?.delta?.content) {
            delta.content = choice.delta.content;
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

          if (delta.content || delta.finish_reason || delta.usage) {
            yield delta;
          }
        } catch {
          // skip malformed
        }
      }
    }
  }

  return { reader: read(), abort: () => controller.abort() };
}

// ── REST API ─────────────────────────────────────────────────────

export const chatApi = {
  // Models
  listModels: () =>
    request<{ data: ModelAlias[] }>("/models").then((r) => r.data),

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
    body: Partial<{ title: string; status: string; project_id: string }>,
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
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`API ${res.status}: ${text}`);
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
