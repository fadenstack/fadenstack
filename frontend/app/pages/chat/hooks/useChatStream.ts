/**
 * useChatStream — hook that manages streaming chat completion lifecycle.
 *
 * Handles: session creation (lazy), message state, SSE streaming, abort,
 * file attachments, and token‐usage extraction.
 */
import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { chatApi, resumeStream, retryTurn, streamChat, type StreamHandle } from "~/api/chatClient";
import { RETRYABLE_TURNS, type ChatMessage, type ChatSession, type TokenUsage } from "~/api/chatTypes";

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
  const [messages, setMessagesState] = useState<ChatMessage[]>([]);
  const messagesRef = useRef<ChatMessage[]>([]);
  // The messages as the callbacks read them: set with the state.
  const setMessages = useCallback((next: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => {
    setMessagesState((prev) => {
      const value = typeof next === "function" ? next(prev) : next;
      messagesRef.current = value;
      return value;
    });
  }, []);
  const [streamingContent, setStreamingContent] = useState("");
  const streamThrottle = useStreamThrottle(setStreamingContent);
  const [streamingReasoning, setStreamingReasoning] = useState("");
  const reasoningThrottle = useStreamThrottle(setStreamingReasoning);
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
    skills?: string[];
  } | null>(null);

  // Load existing messages for the current session
  const loadHistory = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    setIsLoading(true);
    setError(null);
    try {
      const msgs = await chatApi.listMessages(sid);
      // A reply still being written is shown as it streams, not as its row.
      const writing = msgs[msgs.length - 1]?.status === "running";
      setMessages(writing ? msgs.slice(0, -1) : msgs);

      // Check if there is an active stream we can reconnect to
      try {
        const { active } = await chatApi.streamStatus(sid);
        if (active || writing) {
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
              setStreamingUsage(delta.usage);
            }
          }

          // Stream complete: the reply as the gateway stored it.
          streamThrottle.flush();
          setStreamingContent("");
          reasoningThrottle.reset();
          setStreamingReasoning("");
          setStreamingUsage(null);
          setIsStreaming(false);
          abortRef.current = null;
          setMessages(await chatApi.listMessages(sid));
          return;
        }
      } catch {
        // Stream status check failed — fall through to retry prompt
      }

      // If the last message is from the user with no assistant reply,
      // the previous request likely failed — surface a retry prompt.
      // (A failed reply the gateway stored shows itself, with its error.)
      if (msgs.length > 0 && msgs[msgs.length - 1].role === "user") {
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

  /**
   * Read a reply as it streams, then keep it. A reply that failed is shown
   * as the gateway stored it (with its error, to be tried again); one the
   * user stopped keeps what was written.
   */
  const runStream = useCallback(
    async (handle: StreamHandle, sid: string, model: string) => {
      setIsStreaming(true);
      streamThrottle.reset();
      setStreamingContent("");
      reasoningThrottle.reset();
      setStreamingReasoning("");
      setStreamingUsage(null);
      abortRef.current = handle.abort;

      const streamStartedAt = performance.now();
      let accumulated = "";
      let thought = "";
      let thoughtFrom: number | null = null;
      let thoughtMs: number | null = null;
      let finalUsage: TokenUsage | null = null;
      let traceId: string | null = null;
      let turnId: string | null = null;

      const settle = () => {
        streamThrottle.flush();
        setStreamingContent("");
        reasoningThrottle.reset();
        setStreamingReasoning("");
        setStreamingUsage(null);
        setIsStreaming(false);
        abortRef.current = null;
      };
      const reply = (status: ChatMessage["status"]): ChatMessage => ({
        id: turnId ?? `tmp-${Date.now()}-a`,
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
        status,
      });

      try {
        for await (const delta of handle.reader) {
          if (delta.turn_id) turnId = delta.turn_id;
          if (delta.trace_id) traceId = delta.trace_id;
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
        }
        if (!accumulated.trim()) {
          throw new Error(
            t("error_no_response", {
              ns: "chat",
              defaultValue: "No response received from the model.",
            }),
          );
        }
      } catch (err) {
        settle();
        if ((err as Error).name === "AbortError") {
          // Stopped: what was written stays, as the gateway keeps it.
          if (accumulated.trim()) setMessages((prev) => [...prev, reply("stopped")]);
          return;
        }
        // The gateway stored the failed reply before it closed the stream:
        // show it as stored, with its error and a way to try it again.
        try {
          const stored = await chatApi.listMessages(sid);
          setMessages(stored);
          const last = stored[stored.length - 1];
          if (last?.role === "assistant" && last.status && RETRYABLE_TURNS.has(last.status)) {
            setError(null);
            return;
          }
        } catch {
          // Shown below instead.
        }
        setError(err instanceof Error ? err.message : String(err));
        return;
      }

      // Streaming complete — materialise assistant message
      const elapsedMs = Math.round(performance.now() - streamStartedAt);
      const assistantMsg = reply("complete");
      setMessages((prev) => [...prev, assistantMsg]);
      responseMsMapRef.current.set(assistantMsg.id, elapsedMs);
      setLastResponseMs(elapsedMs);
      settle();

      // Update session title if it was auto-generated
      if (onSessionUpdated) {
        try {
          onSessionUpdated(await chatApi.getSession(sid));
        } catch {
          // Non-critical
        }
      }
    },
    [onSessionUpdated],
  );

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

        if (!model) {
          throw new Error(
            t("error_select_model", {
              ns: "chat",
              defaultValue: "Please select a model before sending a message.",
            }),
          );
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return;
      }

      // 4. Stream the reply
      await runStream(
        streamChat({
          session_id: sid,
          model,
          messages: [{ role: "user", content: text }],
          // The gateway takes them off before the model sees the request.
          ...(skills && skills.length > 0 ? { skills } : {}),
        }),
        sid,
        model,
      );
    },
    [onSessionCreated, runStream],
  );

  const retry = useCallback(async () => {
    const sid = sessionIdRef.current;
    const model = selectedModelRef.current || lastSendRef.current?.model || "";
    const last = messagesRef.current[messagesRef.current.length - 1];
    setError(null);

    // The chat's last reply, stored as failed or stopped: a new one replaces it.
    if (
      sid &&
      last?.role === "assistant" &&
      last.status &&
      RETRYABLE_TURNS.has(last.status) &&
      !last.id.startsWith("tmp-")
    ) {
      setMessages((prev) => prev.filter((m) => m.id !== last.id));
      const skills = lastSendRef.current?.skills;
      await runStream(
        retryTurn(sid, last.id, {
          ...(model ? { model } : {}),
          ...(skills && skills.length > 0 ? { skills } : {}),
        }),
        sid,
        model || last.model_alias || "",
      );
      return;
    }

    const lastSend = lastSendRef.current;
    if (!lastSend) return;

    // Reload messages from the server to get the authoritative state.
    // This removes any optimistic duplicates and picks up messages
    // that were persisted on the previous (failed) attempt.
    if (sid) {
      try {
        const freshMsgs = await chatApi.listMessages(sid);
        setMessages(freshMsgs);
      } catch {
        // Fall back to local state if fetch fails
      }
    }

    await send(lastSend.text, model, lastSend.files, lastSend.skills);
  }, [send, runStream]);

  return {
    messages,
    streamingContent,
    streamingReasoning,
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
