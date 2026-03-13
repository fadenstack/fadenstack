/**
 * useChatStream — hook that manages streaming chat completion lifecycle.
 *
 * Handles: session creation (lazy), message state, SSE streaming, abort,
 * file attachments, and token‐usage extraction.
 */
import { useCallback, useRef, useState } from "react";

import { chatApi, streamChat } from "~/api/chatClient";
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

export interface UseChatStreamOptions {
  sessionId?: string;
  onSessionCreated?: (session: ChatSession) => void;
  onSessionUpdated?: (session: ChatSession) => void;
}

export interface UseChatStreamReturn {
  messages: ChatMessage[];
  streamingContent: string;
  streamingUsage: TokenUsage | null;
  isStreaming: boolean;
  isLoading: boolean;
  error: string | null;
  /** Response time in ms for the last completed assistant message. */
  lastResponseMs: number | null;
  /** Get response time for a specific message id, if tracked. */
  getResponseMs: (msgId: string) => number | null;
  send: (text: string, model: string, files?: File[]) => Promise<void>;
  /** Retry the last failed request. */
  retry: () => Promise<void>;
  stop: () => void;
  loadHistory: () => Promise<void>;
}

export function useChatStream({
  sessionId,
  onSessionCreated,
  onSessionUpdated,
}: UseChatStreamOptions): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingContent, setStreamingContent] = useState("");
  const streamThrottle = useStreamThrottle(setStreamingContent);
  const [streamingUsage, setStreamingUsage] = useState<TokenUsage | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
  } | null>(null);

  // Load existing messages for the current session
  const loadHistory = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    setIsLoading(true);
    try {
      const msgs = await chatApi.listMessages(sid);
      setMessages(msgs);

      // If the last message is from the user with no assistant reply,
      // the previous request likely failed — surface a retry prompt.
      if (msgs.length > 0 && msgs[msgs.length - 1].role === "user") {
        const lastUser = msgs[msgs.length - 1];
        lastSendRef.current = { text: lastUser.content, model: "" };
        setError("The last message did not receive a response. You can retry.");
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
    async (text: string, model: string, files?: File[]) => {
      setError(null);
      lastSendRef.current = { text, model, files };
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

        // 3. Append the user message optimistically
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
        setMessages((prev) => [...prev, userMsg]);

        // 4. Start streaming
        setIsStreaming(true);
        streamThrottle.reset();
        setStreamingContent("");
        setStreamingUsage(null);

        const streamStartedAt = performance.now();

        const { reader, abort } = streamChat({
          session_id: sid,
          model: model || undefined,
          messages: [{ role: "user", content: text }],
        });
        abortRef.current = abort;

        let accumulated = "";
        let finalUsage: TokenUsage | null = null;

        for await (const delta of reader) {
          if (delta.content) {
            accumulated += delta.content;
            streamThrottle.push(accumulated);
          }
          if (delta.usage) {
            finalUsage = delta.usage;
            setStreamingUsage(delta.usage);
          }
        }

        // 5. Streaming complete — materialise assistant message
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
          token_estimate: finalUsage?.total_tokens ?? null,
          trace_id: null,
          created_at: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, assistantMsg]);
        responseMsMapRef.current.set(assistantMsgId, elapsedMs);
        setLastResponseMs(elapsedMs);
        streamThrottle.flush();
        setStreamingContent("");
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
          setError(err instanceof Error ? err.message : String(err));
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
    // If the last user message was optimistic (tmp-*), remove it so
    // send() can re-add it. If it's a persisted server message (real
    // id from a reload), keep it — the gateway already has it in the
    // session and send() will append a new optimistic copy that we
    // deduplicate by reloading history on success.
    setMessages((prev) => {
      const lastUserIdx = prev.findLastIndex((m) => m.role === "user");
      if (lastUserIdx === -1) return prev;
      const lastUser = prev[lastUserIdx];
      if (lastUser.id.startsWith("tmp-")) {
        return prev.slice(0, lastUserIdx);
      }
      return prev;
    });
    setError(null);
    await send(last.text, last.model, last.files);
  }, [send]);

  return {
    messages,
    streamingContent,
    streamingUsage,
    isStreaming,
    isLoading,
    error,
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
