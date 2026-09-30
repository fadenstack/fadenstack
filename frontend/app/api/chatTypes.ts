/**
 * Shared TypeScript types for chat entities.
 *
 * Both the admin client (`chat.ts`) and the user-facing client
 * (`chatClient.ts`) import from here to avoid duplication.
 */

export interface ChatProject {
  id: string;
  tenant_id: string;
  user_id: string;
  name: string;
  description: string | null;
  system_instructions?: string | null;
  model_alias: string | null;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ChatSession {
  id: string;
  tenant_id: string;
  user_id: string;
  project_id: string | null;
  title: string | null;
  /** Admin lists: the title is encrypted at rest and not shown (only to its user). */
  title_encrypted?: boolean;
  status: string;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

/** Where a reply is: its row is written as it starts, and kept however it ends. */
export type TurnStatus = "running" | "complete" | "failed" | "stopped" | "interrupted";

/** Replies that can be tried again (the chat's last one). */
export const RETRYABLE_TURNS: ReadonlySet<TurnStatus> = new Set(["failed", "stopped", "interrupted"]);

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  content_parts: unknown[] | null;
  tool_call_json: unknown | null;
  model_alias: string | null;
  provider_instance_id: string | null;
  /** The message's own size in tokens (a reply's completion tokens). */
  token_estimate: number | null;
  trace_id: string | null;
  created_at: string;
  /** What a reply's request used, as logged by the gateway (null if unknown). */
  usage?: TokenUsage | null;
  /** What a reasoning model thought first (this page only; not saved). */
  reasoning?: string | null;
  /** How long it thought, in ms. */
  reasoning_ms?: number | null;
  /** A reply's state; every other message is complete. */
  status?: TurnStatus;
  /** Why a failed reply failed. */
  error?: string | null;
}

export interface ChatAttachment {
  id: string;
  tenant_id: string;
  user_id: string;
  session_id: string | null;
  project_id: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  extraction_status: string;
  scope: string;
  page_count: number | null;
  truncated: boolean;
  created_at: string;
}

export interface ChatStats {
  total_projects: number;
  total_sessions: number;
  total_attachments: number;
  total_attachment_bytes: number;
}

export interface ModelAlias {
  alias: string;
  description: string | null;
  enabled: boolean;
  /**
   * What it is for, when the gateway knows: "chat", "embeddings", "scoring".
   * Null for a route that does not say -- treated as chat, which is what
   * every route was before the gateway could hold other kinds.
   */
  kind?: string | null;
}

/** Whether a model can hold a conversation: an embedding or rerank model cannot. */
export function isChatModel(model: ModelAlias): boolean {
  return !model.kind || model.kind === "chat";
}

export interface Capacity {
  projects: {
    current: number;
    limit: number | null;
  };
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

/** A single SSE delta parsed from a streaming response. */
export interface StreamDelta {
  content?: string;
  /** A reasoning model's thinking, before (or between) the answer's text. */
  reasoning?: string;
  finish_reason?: string | null;
  usage?: TokenUsage;
  trace_id?: string;
  /** The reply's row in the chat (its turn), from the stream's headers. */
  turn_id?: string;
}
