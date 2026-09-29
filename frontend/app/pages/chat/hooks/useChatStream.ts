/**
 * useChatStream — hook that manages streaming chat completion lifecycle.
 *
 * Handles: session creation (lazy), message state, SSE streaming, abort,
 * file attachments, and token‐usage extraction.
 */
import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ChatApiError, chatApi, resumeStream, streamChat } from "~/api/chatClient";
import type { ChatMessage, ChatSession, TokenUsage } from "~/api/chatTypes";

/** Small helper: flush accumulated text to state at display frame rate. */
function useStreamThrottle(setter: (v: string) => void) {
  const accRef = useRef("");
  const rafRef = useRef<number | null>(null);

  const push = useCallback(
    (text: string) => {
      accRef.current = text;
      if (rafRef.current === null) {
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = null;
          setter(accRef.current);
        });
      }
    },
    [setter],
  );

  const flush = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setter(accRef.current);
  }, [setter]);

  const reset = useCallback(() => {
    accRef.current = "";
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  return { push, flush, reset };
}

/** How the last error shows on its turn. */
export interface ChatErrorDetail {
  /** ``blocked``: a governance check refused the prompt (not an error: info,
   * with "edit and resend"); ``rate_limit``: try again later; ``error``: retry. */
  kind: "blocked" | "rate_limit" | "error";
  message: string;
  /** The text that was refused, for "edit and resend". */
  text?: string;
  retryAfter?: number | null;
}

export function errorDetail(err: unknown, text?: string): ChatErrorDetail {
  const message = err instanceof Error ? err.message : String(err);
  if (err instanceof ChatApiError) {
    if (err.code === "policy_blocked" || err.type === "governance_error") {
      return { kind: "blocked", message: err.governance?.message ?? message, text };
    }
    if (err.status === 429 || err.type === "rate_limit_error" || err.code?.startsWith("rate_limit")) {
      return { kind: "rate_limit", message, retryAfter: err.retryAfter };
    }
  }
  return { kind: "error", message };
}

export interface UseChatStreamOptions {
  sessionId?: string;
  selectedModel?: string;
  onSessionCreated?: (session: ChatSession) => void;
  onSessionUpdated?: (session: ChatSession) => void;
}

export interface UseChatStreamReturn {
  messages: ChatMessage[];
  streamingContent: string;
  /** What a reasoning model has thought so far in the reply that streams. */
  streamingReasoning: string;
  streamingUsage: TokenUsage | null;
  isStreaming: boolean;
  isLoading: boolean;
  error: string | null;
  /** The same error, with how to show it (refused, rate limited, failed). */
  errorInfo: ChatErrorDetail | null;
  /** Response time in ms for the last completed assistant message. */
  lastResponseMs: number | null;
  /** Get response time for a specific message id, if tracked. */
  getResponseMs: (msgId: string) => number | null;
  /** ``skills``: the slugs picked for the chat, sent with the message. */
  send: (text: string, model: string, files?: File[], skills?: string[]) => Promise<void>;
  /** Retry the last failed request. */
  retry: () => Promise<void>;
  stop: () => void;
  loadHistory: () => Promise<void>;
}

export function useChatStream({
  sessionId,
  selectedModel,
  onSessionCreated,
  onSessionUpdated,
}: UseChatStreamOptions): UseChatStreamReturn {
  const { t } = useTranslation();
  const selectedModelRef = useRef(selectedModel);
  selectedModelRef.current = selectedModel;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingContent, setStreamingContent] = useState("");
  const streamThrottle = useStreamThrottle(setStreamingContent);
  const [streamingReasoning, setStreamingReasoning] = useState("");
  const reasoningThrottle = useStreamThrottle(setStreamingReasoning);
  const [streamingUsage, setStreamingUsage] = useState<TokenUsage | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorInfo, setErrorInfo] = useState<ChatErrorDetail | null>(null);
  const error = errorInfo?.message ?? null;
  const setError = useCallback((message: string | null) => {
    setErrorInfo(message === null ? null : { kind: "error", message });
  }, []);
  const [lastResponseMs, setLastResponseMs] = useState<number | null>(null);
  /** Per-message response times (keyed by message id). */
  const responseMsMapRef = useRef<Map<string, number>>(new Map());

  const abortRef = useRef<(() => void) | null>(null);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  /** Stores the last send args so retry can re-issue the request. */
  const lastSendRef = useRef<{
    text: string;
    model: string;
    files?: File[];
    skills?: string[];
  } | null>(null);

  // Load existing messages for the current session
  const loadHistory = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    setIsLoading(true);
    try {
      const msgs = await chatApi.listMessages(sid);
      setMessages(msgs);

      // Check if there is an active stream we can reconnect to
      try {
        const { active } = await chatApi.streamStatus(sid);
        if (active) {
          // Reconnect to the in-progress stream
          setIsStreaming(true);
          streamThrottle.reset();
          setStreamingContent("");
          reasoningThrottle.reset();
          setStreamingReasoning("");
          setStreamingUsage(null);

          const { reader, abort } = resumeStream(sid);
          abortRef.current = abort;

          let accumulated = "";
          let thought = "";
          let finalUsage: TokenUsage | null = null;
          let traceId: string | null = null;

          for await (const delta of reader) {
            if (delta.reasoning) {
              thought += delta.reasoning;
              reasoningThrottle.push(thought);
            }
            if (delta.content) {
              accumulated += delta.content;
              streamThrottle.push(accumulated);
            }
            if (delta.usage) {
              finalUsage = delta.usage;
              setStreamingUsage(delta.usage);
            }
            if (delta.trace_id) {
              traceId = delta.trace_id;
            }
          }

          // Stream complete — materialise assistant message
          if (accumulated.trim()) {
            const assistantMsg: ChatMessage = {
              id: `tmp-${Date.now()}-a`,
              session_id: sid,
              role: "assistant",
              content: accumulated,
              content_parts: null,
              tool_call_json: null,
              model_alias: null,
              provider_instance_id: null,
              token_estimate: finalUsage?.completion_tokens ?? null,
              trace_id: traceId,
              usage: finalUsage,
              reasoning: thought || null,
              created_at: new Date().toISOString(),
            };
            setMessages((prev) => [...prev, assistantMsg]);
          }

          streamThrottle.flush();
          setStreamingContent("");
          reasoningThrottle.reset();
          setStreamingReasoning("");
          setStreamingUsage(null);
          setIsStreaming(false);
          abortRef.current = null;
          return;
        }
      } catch {
        // Stream status check failed — fall through to retry prompt
      }

      // If the last message is from the user with no assistant reply,
      // the previous request likely failed — surface a retry prompt.
      // A refused prompt got its answer: the notice (status blocked_input).
      if (
        msgs.length > 0 &&
        msgs[msgs.length - 1].role === "user" &&
        msgs[msgs.length - 1].status !== "blocked_input"
      ) {
        const lastUser = msgs[msgs.length - 1];
        lastSendRef.current = { text: lastUser.content, model: "" };
        setError(
          t("error_last_message_no_response", {
            ns: "chat",
            defaultValue:
              "The last message did not receive a response. You can retry.",
          }),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsLoading(false);
    }
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  const send = useCallback(
    async (text: string, model: string, files?: File[], skills?: string[]) => {
      setError(null);
      lastSendRef.current = { text, model, files, skills };
      let sid = sessionIdRef.current;

      try {
        // 1. Create session lazily if needed
        if (!sid) {
          const session = await chatApi.createSession({
            title: text.slice(0, 100),
          });
          sid = session.id;
          sessionIdRef.current = sid;
          onSessionCreated?.(session);
        }

        // 2. Upload attachments (if any)
        if (files && files.length > 0) {
          await Promise.all(
            files.map((f) => chatApi.uploadAttachment(sid!, f)),
          );
        }

        // 3. Append the user message optimistically (skip if duplicate)
        const userMsg: ChatMessage = {
          id: `tmp-${Date.now()}`,
          session_id: sid,
          role: "user",
          content: text,
          content_parts: null,
          tool_call_json: null,
          model_alias: null,
          provider_instance_id: null,
          token_estimate: null,
          trace_id: null,
          created_at: new Date().toISOString(),
        };
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === "user" && last.content === text) {
            return prev;
          }
          return [...prev, userMsg];
        });

        // 4. Start streaming
        setIsStreaming(true);
        streamThrottle.reset();
        setStreamingContent("");
        reasoningThrottle.reset();
        setStreamingReasoning("");
        setStreamingUsage(null);

        const streamStartedAt = performance.now();

        if (!model) {
          throw new Error(
            t("error_select_model", {
              ns: "chat",
              defaultValue: "Please select a model before sending a message.",
            }),
          );
        }

        const { reader, abort } = streamChat({
          session_id: sid,
          model,
          messages: [{ role: "user", content: text }],
          // The gateway takes them off before the model sees the request.
          ...(skills && skills.length > 0 ? { skills } : {}),
        });
        abortRef.current = abort;

        let accumulated = "";
        let thought = "";
        let thoughtFrom: number | null = null;
        let thoughtMs: number | null = null;
        let finalUsage: TokenUsage | null = null;
        let traceId: string | null = null;
        let finish: string | null = null;

        for await (const delta of reader) {
          if (delta.reasoning) {
            thoughtFrom ??= performance.now();
            thought += delta.reasoning;
            reasoningThrottle.push(thought);
          }
          if (delta.content) {
            if (thoughtFrom !== null && thoughtMs === null) {
              thoughtMs = Math.round(performance.now() - thoughtFrom);
            }
            accumulated += delta.content;
            streamThrottle.push(accumulated);
          }
          if (delta.usage) {
            finalUsage = delta.usage;
            setStreamingUsage(delta.usage);
          }
          if (delta.trace_id) {
            traceId = delta.trace_id;
          }
          if (delta.finish_reason) {
            finish = delta.finish_reason;
          }
        }

        // A refused answer: the part already shown is not kept -- the session
        // holds the notice in its place.
        if (finish === "content_filter") {
          try {
            setMessages(await chatApi.listMessages(sid));
          } catch {
            // The notice shows on the next load.
          }
          streamThrottle.reset();
          setStreamingContent("");
          reasoningThrottle.reset();
          setStreamingReasoning("");
          setStreamingUsage(null);
          setIsStreaming(false);
          abortRef.current = null;
          return;
        }

        // 5. Streaming complete — materialise assistant message
        if (!accumulated.trim()) {
          throw new Error(
            t("error_no_response", {
              ns: "chat",
              defaultValue: "No response received from the model.",
            }),
          );
        }
        const elapsedMs = Math.round(performance.now() - streamStartedAt);
        const assistantMsgId = `tmp-${Date.now()}-a`;
        const assistantMsg: ChatMessage = {
          id: assistantMsgId,
          session_id: sid,
          role: "assistant",
          content: accumulated,
          content_parts: null,
          tool_call_json: null,
          model_alias: model || null,
          provider_instance_id: null,
          token_estimate: finalUsage?.completion_tokens ?? null,
          trace_id: traceId,
          usage: finalUsage,
          reasoning: thought || null,
          reasoning_ms: thoughtMs,
          created_at: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, assistantMsg]);
        responseMsMapRef.current.set(assistantMsgId, elapsedMs);
        setLastResponseMs(elapsedMs);
        streamThrottle.flush();
        setStreamingContent("");
        reasoningThrottle.reset();
        setStreamingReasoning("");
        setStreamingUsage(null);
        setIsStreaming(false);
        abortRef.current = null;

        // 6. Update session title if it was auto-generated
        if (onSessionUpdated) {
          try {
            const updated = await chatApi.getSession(sid);
            onSessionUpdated(updated);
          } catch {
            // Non-critical
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          const detail = errorDetail(err, text);
          if (detail.kind === "blocked" && sid) {
            // The session keeps the refused turn as a notice, without its text.
            try {
              setMessages(await chatApi.listMessages(sid));
            } catch {
              // Keep the optimistic message.
            }
          }
          setErrorInfo(detail);
        }
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [onSessionCreated, onSessionUpdated],
  );

  const retry = useCallback(async () => {
    const last = lastSendRef.current;
    if (!last) return;
    // Use the currently selected model, not the one from the failed attempt
    const model = selectedModelRef.current || last.model;
    setError(null);

    // Reload messages from the server to get the authoritative state.
    // This removes any optimistic duplicates and picks up messages
    // that were persisted on the previous (failed) attempt.
    const sid = sessionIdRef.current;
    if (sid) {
      try {
        const freshMsgs = await chatApi.listMessages(sid);
        setMessages(freshMsgs);
      } catch {
        // Fall back to local state if fetch fails
      }
    }

    await send(last.text, model, last.files, last.skills);
  }, [send]);

  return {
    messages,
    streamingContent,
    streamingReasoning,
    streamingUsage,
    isStreaming,
    isLoading,
    error,
    errorInfo,
    lastResponseMs,
    /** Get response time for a specific message id, if tracked. */
    getResponseMs: (msgId: string) =>
      responseMsMapRef.current.get(msgId) ?? null,
    send,
    retry,
    stop,
    loadHistory,
  };
}
