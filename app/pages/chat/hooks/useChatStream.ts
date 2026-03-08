/**
 * useChatStream — hook that manages streaming chat completion lifecycle.
 *
 * Handles: session creation (lazy), message state, SSE streaming, abort,
 * file attachments, and token‐usage extraction.
 */
import { useCallback, useRef, useState } from "react";

import { chatApi, streamChat } from "~/api/chatClient";
import type {
  ChatMessage,
  ChatSession,
  TokenUsage,
  StreamDelta,
} from "~/api/chatTypes";

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
  send: (text: string, model: string, files?: File[]) => Promise<void>;
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
  const [streamingUsage, setStreamingUsage] = useState<TokenUsage | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<(() => void) | null>(null);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  // Load existing messages for the current session
  const loadHistory = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    setIsLoading(true);
    try {
      const msgs = await chatApi.listMessages(sid);
      setMessages(msgs);
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
        setStreamingContent("");
        setStreamingUsage(null);

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
            setStreamingContent(accumulated);
          }
          if (delta.usage) {
            finalUsage = delta.usage;
            setStreamingUsage(delta.usage);
          }
        }

        // 5. Streaming complete — materialise assistant message
        const assistantMsg: ChatMessage = {
          id: `tmp-${Date.now()}-a`,
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

  return {
    messages,
    streamingContent,
    streamingUsage,
    isStreaming,
    isLoading,
    error,
    send,
    stop,
    loadHistory,
  };
}
