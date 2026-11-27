/**
 * useChatStream — hook that manages streaming chat completion lifecycle.
 *
 * Handles: session creation (lazy), message state, SSE streaming, abort,
 * file attachments, and token‐usage extraction.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  BUDGET_EXCEEDED,
  ChatApiError,
  chatApi,
  resumeStream,
  retryTurn,
  streamChat,
  type StreamHandle,
} from "~/api/chatClient";
import {
  CONTEXT_LENGTH_EXCEEDED,
  RETRYABLE_TURNS,
  type ChatMessage,
  type ChatSession,
  type ContextUse,
  type FadenEvent,
  type TokenUsage,
} from "~/api/chatTypes";
import { localPictureParts, withPictures } from "../messagePictures";

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
   * with "edit and resend"); ``rate_limit``: try again later; ``budget``: a
   * budget that applies is used up until its period ends; ``error``: retry. */
  kind: "blocked" | "rate_limit" | "budget" | "pictures_refused" | "error";
  message: string;
  /** The text that was refused, for "edit and resend". */
  text?: string;
  retryAfter?: number | null;
  /** ``budget``: the kind of budget (``personal``, ``team``, ``agent``, ``organisation``). */
  param?: string | null;
  /** ``pictures_refused``: the project's files that are pictures, the first one is named. */
  files?: string[];
}

export function errorDetail(err: unknown, text?: string): ChatErrorDetail {
  const message = err instanceof Error ? err.message : String(err);
  if (err instanceof ChatApiError) {
    if (err.code === "policy_blocked" || err.type === "governance_error") {
      return { kind: "blocked", message: err.governance?.message ?? message, text };
    }
    if (err.code === "pii_pictures_refused") {
      // Worded in the reader's language where it shows (ChatWindow).
      return { kind: "pictures_refused", message, text, files: err.projectPictures };
    }
    if (err.code === BUDGET_EXCEEDED) {
      return { kind: "budget", message, retryAfter: err.retryAfter, param: err.param };
    }
    if (err.status === 429 || err.type === "rate_limit_error" || err.code?.startsWith("rate_limit")) {
      return { kind: "rate_limit", message, retryAfter: err.retryAfter };
    }
  }
  return { kind: "error", message };
}

/** The line that says older messages no longer go to the model: once per chat, where they stopped. */
export interface OlderNote {
  /** A summary written by a model stands for them. */
  summary: boolean;
  /** The line stands before the message at this index (the one the turn answers). */
  index: number;
}

/** A tool call that waits for the user in the answer that streams. */
export interface PendingApproval {
  approvalId: string;
  toolName: string;
  message: string;
  timeoutS: number | null;
  /** The tool's arguments as the gateway sent them: a JSON string, cut at a limit. */
  arguments: string;
  argumentsTruncated: boolean;
  argumentsSha256: string;
}

/** A file of the chat the gateway did not send to the model (``faden.attachment_left_out``). */
export interface LeftOutFile {
  /** Why: ``no_vision``, a picture for a model that does not take pictures. */
  reason: string;
  file: string;
  model: string;
  /** The gateway's own words, for a reason the page does not know. */
  message: string;
}

/** What a gateway event means for the page; ``null`` for the ones it does not show. */
export function readFadenEvent(event: FadenEvent):
  | { kind: "approval"; approval: PendingApproval }
  | { kind: "denied"; notice: string }
  | { kind: "left_out"; leftOut: LeftOutFile }
  | { kind: "pictures_unchecked"; count: number }
  | { kind: "unmarked" }
  | { kind: "context"; context: ContextUse }
  | null {
  const value = (event.value ?? {}) as Record<string, unknown>;
  const text = (key: string) => (typeof value[key] === "string" ? (value[key] as string) : "");
  if (event.name === "faden.context") {
    const count = (key: string) => (typeof value[key] === "number" && value[key] >= 0 ? (value[key] as number) : 0);
    return {
      kind: "context",
      context: {
        tokens: count("tokens"),
        budget: count("budget"),
        window: typeof value.window === "number" && value.window > 0 ? value.window : null,
        dropped: count("dropped"),
        summary: value.summary === true,
        summarised: value.summarised === true,
      },
    };
  }
  if (event.name === "faden.attachment_left_out") {
    return {
      kind: "left_out",
      leftOut: { reason: text("reason"), file: text("filename"), model: text("model"), message: text("message") },
    };
  }
  if (event.name === "faden.pii_pictures_unchecked") {
    const count = typeof value.count === "number" && value.count >= 1 ? Math.floor(value.count) : 1;
    return { kind: "pictures_unchecked", count };
  }
  if (event.name === "faden.approval_required" && text("approvalId")) {
    return {
      kind: "approval",
      approval: {
        approvalId: text("approvalId"),
        toolName: text("toolCallName"),
        message: text("message"),
        timeoutS: typeof value.timeoutS === "number" ? value.timeoutS : null,
        arguments: text("arguments"),
        argumentsTruncated: value.argumentsTruncated === true,
        argumentsSha256: text("argumentsSha256"),
      },
    };
  }
  // The model answers without a mark that shows the answer as AI-generated: an administrator allowed it.
  if (event.name === "faden.unmarked") return { kind: "unmarked" };
  if (event.name === "faden.tool_denied") {
    const name = text("toolCallName");
    return { kind: "denied", notice: name ? `${name}: ${text("message")}` : text("message") };
  }
  return null;
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
  /** A tool call in the streaming answer that waits for the user. */
  pendingApproval: PendingApproval | null;
  /** Approve or decline it; the answer goes on. */
  answerApproval: (approved: boolean) => Promise<void>;
  /** Tools refused during the answer that streams (by the tool mode or a rule). */
  toolNotices: string[];
  /** Files of the chat not sent with the answer that streams (a picture the model cannot see). */
  streamingNotes: string[];
  /** The same for a reply, once written (this page only; not saved). */
  getReplyNotes: (msgId: string) => string[];
  /** What the last turn said about its context (`faden.context`): how much went, of what budget. */
  context: ContextUse | null;
  /** The line for the chat once older messages stopped going to the model; null before. */
  olderNote: OlderNote | null;
  /** Response time in ms for the last completed assistant message. */
  lastResponseMs: number | null;
  /** Get response time for a specific message id, if tracked. */
  getResponseMs: (msgId: string) => number | null;
  /** ``skills``: the slugs picked for the chat, sent with the message. */
  send: (
    text: string,
    model: string,
    files?: File[],
    skills?: string[],
    /** The thinking level as the gateway takes it (`none`, `low`, ...); nothing for Auto. */
    reasoningEffort?: string,
  ) => Promise<void>;
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
      // A list read from the gateway keeps the pictures this page showed on the messages it sent.
      const value = typeof next === "function" ? next(prev) : withPictures(prev, next);
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
  const [errorInfo, setErrorInfo] = useState<ChatErrorDetail | null>(null);
  const error = errorInfo?.message ?? null;
  const setError = useCallback((message: string | null) => {
    setErrorInfo(message === null ? null : { kind: "error", message });
  }, []);
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [context, setContext] = useState<ContextUse | null>(null);
  /** The line on older messages, by chat: said once per chat for the page's life. */
  const [olderNotes, setOlderNotes] = useState<Record<string, OlderNote>>({});
  const olderNotesRef = useRef(olderNotes);
  olderNotesRef.current = olderNotes;
  const [toolNotices, setToolNotices] = useState<string[]>([]);
  const [streamingNotes, setStreamingNotes] = useState<string[]>([]);
  const [replyNotes, setReplyNotes] = useState<Record<string, string[]>>({});
  const noteOf = useCallback(
    (left: LeftOutFile) =>
      left.reason === "no_vision" && left.file
        ? t("picture_not_sent", {
            ns: "chat",
            defaultValue: "{{file}} was not sent: {{model}} does not take pictures. Choose a model that does.",
            file: left.file,
            model: left.model,
          })
        : left.reason === "unreadable" && left.file
          ? t("picture_unreadable", {
              ns: "chat",
              defaultValue: "{{file}} was not sent: its text could not be read.",
              file: left.file,
            })
          : left.message,
    [t],
  );
  /** Chats that showed the note on unchecked pictures: a project's picture goes with every turn. */
  const uncheckedShownRef = useRef<Set<string>>(new Set());
  const uncheckedNote = useCallback(
    (count: number, names: string[]) => {
      // The name only when one picture went and the page knows whose.
      if (count === 1 && names.length === 1) {
        return t("pictures_unchecked_file", {
          ns: "chat",
          defaultValue: "Pictures are not checked for personal data, so {{file}} went to the model as it is.",
          file: names[0],
        });
      }
      if (count > 1) {
        return t("pictures_unchecked_many", {
          ns: "chat",
          defaultValue: "Pictures are not checked for personal data, so {{count}} pictures went to the model as they are.",
          count,
        });
      }
      return t("pictures_unchecked_none", {
        ns: "chat",
        defaultValue: "Pictures are not checked for personal data, so the picture went to the model as it is.",
      });
    },
    [t],
  );
  const onFaden = useCallback((event: FadenEvent) => {
    const read = readFadenEvent(event);
    if (read?.kind === "approval") setPendingApproval(read.approval);
    if (read?.kind === "denied") setToolNotices((prev) => [...prev, read.notice]);
    if (read?.kind === "unmarked") setToolNotices((prev) => [...prev, t("chat.unmarked_notice")]);
  }, [t]);
  const answerApproval = useCallback(
    async (approved: boolean) => {
      const waiting = pendingApproval;
      if (!waiting) return;
      setPendingApproval(null);
      try {
        await chatApi.answerApproval(waiting.approvalId, approved);
      } catch (err) {
        setErrorInfo(errorDetail(err));
      }
    },
    [pendingApproval],
  );
  const [lastResponseMs, setLastResponseMs] = useState<number | null>(null);
  /** Per-message response times (keyed by message id). */
  const responseMsMapRef = useRef<Map<string, number>>(new Map());

  const abortRef = useRef<(() => void) | null>(null);
  /** The reply being written, which Stop stops. */
  const turnRef = useRef<string | null>(null);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
  /** The notes of replies (this page only), for a retry to read. */
  const replyNotesRef = useRef(replyNotes);
  replyNotesRef.current = replyNotes;
  /** Object URLs made for the pictures of sent messages: let go of with the chat. */
  const pictureUrlsRef = useRef<string[]>([]);
  useEffect(
    () => () => {
      for (const url of pictureUrlsRef.current) URL.revokeObjectURL(url);
      pictureUrlsRef.current = [];
    },
    [],
  );

  /** Stores the last send args so retry can re-issue the request. */
  const lastSendRef = useRef<{
    text: string;
    model: string;
    files?: File[];
    skills?: string[];
    reasoningEffort?: string;
  } | null>(null);

  const stop = useCallback(() => {
    // The reply runs on its own: closing the stream alone would not stop it.
    const sid = sessionIdRef.current;
    const turn = turnRef.current;
    if (sid && turn) void chatApi.stopTurn(sid, turn).catch(() => undefined);
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
    setPendingApproval(null);
  }, []);

  /**
   * Read a reply as it streams, then keep it.
   *
   * A reply that failed is shown as the gateway stored it (with its error, to
   * be tried again); one the user stopped keeps what was written. When the
   * connection is lost while the reply is still being written -- a network
   * change, a laptop waking up -- the page reads it again from the gateway
   * (``resumed``), once. A reply read that way (``resumed``) is shown as
   * stored once it ends.
   */
  const runStream = useCallback(
    async (
      handle: StreamHandle,
      sid: string,
      model: string,
      {
        turn = null,
        resumed = false,
        text,
        pictureNames = [],
        carryNotes = [],
        contextRetry = false,
      }: {
        turn?: string | null;
        resumed?: boolean;
        text?: string;
        /** The names of the pictures of this message, for the note on unchecked pictures. */
        pictureNames?: string[];
        /** What the reply being tried again said was left out: kept, in case the new one does not say it again. */
        carryNotes?: string[];
        /** This run is the one retry after a context that was too long: another overflow is shown as failed. */
        contextRetry?: boolean;
      } = {},
    ): Promise<void> => {
      setIsStreaming(true);
      streamThrottle.reset();
      setStreamingContent("");
      reasoningThrottle.reset();
      setStreamingReasoning("");
      setStreamingUsage(null);
      abortRef.current = handle.abort;
      turnRef.current = turn;

      const streamStartedAt = performance.now();
      let accumulated = "";
      let thought = "";
      let thoughtFrom: number | null = null;
      let thoughtMs: number | null = null;
      let finalUsage: TokenUsage | null = null;
      let traceId: string | null = null;
      let turnId: string | null = turn;
      let finish: string | null = null;
      const localId = `tmp-${Date.now()}-a`;
      // Files of the chat the gateway did not send with this reply: shown on it.
      const notes: string[] = [...carryNotes];

      const settle = () => {
        turnRef.current = null;
        streamThrottle.flush();
        setStreamingContent("");
        reasoningThrottle.reset();
        setStreamingReasoning("");
        setStreamingUsage(null);
        setIsStreaming(false);
        abortRef.current = null;
        setStreamingNotes([]);
        if (notes.length > 0) {
          const id = turnId ?? localId;
          setReplyNotes((prev) => ({ ...prev, [id]: [...notes] }));
        }
      };
      const reply = (status: ChatMessage["status"]): ChatMessage => ({
        id: turnId ?? localId,
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

      setToolNotices([]);
      setStreamingNotes([...notes]);
      try {
        for await (const delta of handle.reader) {
          if (delta.faden) {
            const read = readFadenEvent(delta.faden);
            if (read?.kind === "left_out") {
              const note = noteOf(read.leftOut);
              if (!notes.includes(note)) notes.push(note);
              setStreamingNotes([...notes]);
            }
            if (read?.kind === "pictures_unchecked" && !uncheckedShownRef.current.has(sid)) {
              uncheckedShownRef.current.add(sid);
              notes.push(uncheckedNote(read.count, pictureNames));
              setStreamingNotes([...notes]);
            }
            if (read?.kind === "context") {
              setContext(read.context);
              if (read.context.dropped > 0) {
                const said = olderNotesRef.current[sid];
                const next = said
                  ? said.summary || !read.context.summary
                    ? null
                    : { ...said, summary: true }
                  : { summary: read.context.summary, index: Math.max(0, messagesRef.current.length - 1) };
                if (next) {
                  olderNotesRef.current = { ...olderNotesRef.current, [sid]: next };
                  setOlderNotes(olderNotesRef.current);
                }
              }
            }
            onFaden(delta.faden);
            continue;
          }
          if (delta.turn_id) turnId = turnRef.current = delta.turn_id;
          if (delta.finish_reason) finish = delta.finish_reason;
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
        if (finish === "content_filter") {
          // A refused answer: the part already shown is not kept -- the session
          // holds the notice in its place.
          streamThrottle.reset();
          settle();
          try {
            setMessages(await chatApi.listMessages(sid));
          } catch {
            // The notice shows on the next load.
          }
          return;
        }
        if (!accumulated.trim()) {
          // Ended at the token limit with nothing written: it thought until its length ran out.
          throw new Error(
            finish === "length"
              ? t("error_no_response_length", {
                  ns: "chat",
                  defaultValue:
                    "The model reached its length limit before it answered, for example while thinking. Try again, or ask for less at once.",
                })
              : t("error_no_response", {
                  ns: "chat",
                  defaultValue: "No response received from the model.",
                }),
          );
        }
      } catch (err) {
        settle();
        setPendingApproval(null);
        if ((err as Error).name === "AbortError") {
          // Stopped: what was written stays, as the gateway keeps it.
          if (accumulated.trim()) setMessages((prev) => [...prev, reply("stopped")]);
          return;
        }
        const detail = errorDetail(err, text);
        // Where the reply is now, as the gateway stored it.
        let last: ChatMessage | undefined;
        try {
          const stored = await chatApi.listMessages(sid);
          last = stored[stored.length - 1];
          if (last?.role === "assistant" && last.status === "running") {
            // Still being written: the connection went, not the reply.
            if (!resumed) {
              setMessages(stored.slice(0, -1));
              await runStream(resumeStream(sid), sid, model, { turn: last.id, resumed: true });
              return;
            }
            setMessages(stored);
            return;
          }
          const overflow = err instanceof ChatApiError && err.code === CONTEXT_LENGTH_EXCEEDED;
          const failedTurn =
            last?.role === "assistant" && last.status && RETRYABLE_TURNS.has(last.status) ? last.id : null;
          const turnToRetry = failedTurn ?? (overflow ? ((err as ChatApiError).turnId ?? turnId) : null);
          if (overflow && !contextRetry && turnToRetry && !turnToRetry.startsWith("tmp-")) {
            // The context was too long for the model: once, without asking, with less of it.
            const sent = lastSendRef.current;
            setMessages(failedTurn ? stored.slice(0, -1) : stored);
            await runStream(
              retryTurn(sid, turnToRetry, {
                context_factor: 0.75,
                ...(model ? { model } : {}),
                ...(sent?.skills && sent.skills.length > 0 ? { skills: sent.skills } : {}),
                ...(sent?.reasoningEffort ? { reasoning_effort: sent.reasoningEffort } : {}),
              }),
              sid,
              model,
              { text, contextRetry: true },
            );
            return;
          }
          if (failedTurn) {
            // Failed, and stored with its error, to be tried again.
            setMessages(stored);
            setError(null);
            return;
          }
          // A refused prompt: the session keeps it as a notice, without its text.
          if (resumed || detail.kind === "blocked") setMessages(stored);
        } catch {
          // Shown below instead.
        }
        // Refused before it was kept (a limit, say): what the user wrote
        // stays on the page, with why, to be sent again.
        setErrorInfo(detail);
        return;
      }

      if (resumed) {
        // Read again from the gateway: shown as it stored it.
        settle();
        try {
          setMessages(await chatApi.listMessages(sid));
        } catch {
          setMessages((prev) => [...prev, reply("complete")]);
        }
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
    [onSessionUpdated, onFaden, noteOf, uncheckedNote],
  );

  // Load existing messages for the current session
  const loadHistory = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    setIsLoading(true);
    setError(null);
    let writing: ChatMessage | null = null;
    try {
      const msgs = await chatApi.listMessages(sid);
      // A reply still being written is shown as it streams, not as its row.
      const last = msgs[msgs.length - 1];
      writing = last?.role === "assistant" && last.status === "running" ? last : null;
      setMessages(writing ? msgs.slice(0, -1) : msgs);

      if (!writing) {
        let active = false;
        try {
          ({ active } = await chatApi.streamStatus(sid));
        } catch {
          // Stream status check failed — fall through to retry prompt
        }
        if (
          !active &&
          msgs.length > 0 &&
          msgs[msgs.length - 1].role === "user" &&
          msgs[msgs.length - 1].status !== "blocked_input"
        ) {
          // If the last message is from the user with no assistant reply,
          // the previous request likely failed — surface a retry prompt.
          // (A failed reply the gateway stored shows itself, with its error;
          // a refused prompt got its answer: the notice.)
          const lastUser = msgs[msgs.length - 1];
          lastSendRef.current = { text: lastUser.content, model: "" };
          setError(
            t("error_last_message_no_response", {
              ns: "chat",
              defaultValue:
                "The last message did not receive a response. You can retry.",
            }),
          );
          return;
        }
        if (!active) return;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return;
    } finally {
      setIsLoading(false);
    }
    // Being written, here or on another gateway worker: read it from its start.
    await runStream(resumeStream(sid), sid, writing?.model_alias ?? "", {
      turn: writing?.id ?? null,
      resumed: true,
    });
  }, [runStream]);

  const send = useCallback(
    async (text: string, model: string, files?: File[], skills?: string[], reasoningEffort?: string) => {
      setError(null);
      lastSendRef.current = { text, model, files, skills, reasoningEffort };
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

        // 3. Append the user message optimistically (skip if duplicate),
        // with its pictures, as the composer showed them.
        const pictures = localPictureParts(files);
        pictureUrlsRef.current.push(...pictures.urls);
        const userMsg: ChatMessage = {
          id: `tmp-${Date.now()}`,
          session_id: sid,
          role: "user",
          content: text,
          content_parts: pictures.parts.length > 0 ? pictures.parts : null,
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
          // Auto sends nothing: the model decides.
          ...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
        }),
        sid,
        model,
        {
          text,
          // The names of the pictures of this message, for the note on unchecked pictures.
          pictureNames: (files ?? []).filter((f) => f.type.startsWith("image/")).map((f) => f.name),
        },
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
      const effort = lastSendRef.current?.reasoningEffort;
      await runStream(
        retryTurn(sid, last.id, {
          ...(model ? { model } : {}),
          ...(skills && skills.length > 0 ? { skills } : {}),
          ...(effort ? { reasoning_effort: effort } : {}),
        }),
        sid,
        model || last.model_alias || "",
        // The note the reply had (a picture left out) goes with the new one.
        { carryNotes: replyNotesRef.current[last.id] ?? [] },
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

    await send(lastSend.text, model, lastSend.files, lastSend.skills, lastSend.reasoningEffort);
  }, [send, runStream]);

  return {
    pendingApproval,
    answerApproval,
    toolNotices,
    streamingNotes,
    getReplyNotes: (msgId: string) => replyNotes[msgId] ?? [],
    messages,
    streamingContent,
    streamingReasoning,
    streamingUsage,
    isStreaming,
    isLoading,
    error,
    errorInfo,
    lastResponseMs,
    context,
    olderNote: olderNotes[sessionId ?? sessionIdRef.current ?? ""] ?? null,
    /** Get response time for a specific message id, if tracked. */
    getResponseMs: (msgId: string) =>
      responseMsMapRef.current.get(msgId) ?? null,
    send,
    retry,
    stop,
    loadHistory,
  };
}
