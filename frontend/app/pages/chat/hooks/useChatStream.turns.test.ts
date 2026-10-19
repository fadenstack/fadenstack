/**
 * A reply that failed or stopped is the chat's own: shown as the gateway
 * stored it, and tried again in its place.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StreamHandle } from "~/api/chatClient";
import type { ChatMessage, StreamDelta } from "~/api/chatTypes";

const api = vi.hoisted(() => ({
  listMessages: vi.fn(),
  streamStatus: vi.fn(),
  getSession: vi.fn(),
  stopTurn: vi.fn(),
  streamChat: vi.fn(),
  retryTurn: vi.fn(),
  resumeStream: vi.fn(),
}));

vi.mock("~/api/chatClient", () => ({
  ChatApiError: class ChatApiError extends Error {},
  chatApi: {
    listMessages: api.listMessages, streamStatus: api.streamStatus, getSession: api.getSession, stopTurn: api.stopTurn,
  },
  streamChat: api.streamChat,
  retryTurn: api.retryTurn,
  resumeStream: api.resumeStream,
}));

import { useChatStream } from "./useChatStream";

const SID = "s1";

function message(id: string, role: ChatMessage["role"], content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id, session_id: SID, role, content, content_parts: null, tool_call_json: null, model_alias: "m",
    provider_instance_id: null, token_estimate: null, trace_id: null, created_at: "2026-09-30T12:00:00Z",
    status: "complete", ...extra,
  };
}

/** A stream of *deltas*, then *error* if given. */
function stream(deltas: StreamDelta[], error?: Error): StreamHandle {
  async function* reader() {
    for (const delta of deltas) yield delta;
    if (error) throw error;
  }
  return { reader: reader(), abort: vi.fn() };
}

const FAILED = message("turn-1", "assistant", "", { status: "failed", error: "System message must be at the beginning." });

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.streamStatus.mockResolvedValue({ active: false });
  api.getSession.mockResolvedValue({ id: SID });
});

describe("useChatStream", () => {
  it("shows a failed reply as the gateway stored it, not as a banner", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "turn-1" }], new Error(FAILED.error!)));
    api.listMessages.mockResolvedValue([message("u1", "user", "hi"), FAILED]);
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("hi", "m"));

    expect(result.current.error).toBeNull();
    expect(result.current.messages.at(-1)).toMatchObject({ id: "turn-1", status: "failed", error: FAILED.error });
  });

  it("tries the stored failed reply again in its place", async () => {
    api.listMessages.mockResolvedValue([message("u1", "user", "hi"), FAILED]);
    api.retryTurn.mockReturnValue(stream([{ turn_id: "turn-2", content: "Hello" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "other" }));
    await act(() => result.current.loadHistory());

    await act(() => result.current.retry());

    expect(api.retryTurn).toHaveBeenCalledWith(SID, "turn-1", { model: "other" });
    expect(result.current.messages.map((m) => [m.id, m.status])).toEqual([["u1", "complete"], ["turn-2", "complete"]]);
  });

  it("keeps what was written when the user stops the reply", async () => {
    const aborted = Object.assign(new Error("aborted"), { name: "AbortError" });
    api.streamChat.mockReturnValue(stream([{ turn_id: "turn-1", content: "Half an" }], aborted));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("hi", "m"));

    expect(result.current.error).toBeNull();
    expect(result.current.messages.at(-1)).toMatchObject({ id: "turn-1", content: "Half an", status: "stopped" });
  });

  it("stops the reply at the gateway, not only the stream: it runs on its own", async () => {
    let abort!: () => void;
    const aborted = new Promise<never>((_, reject) => {
      abort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    });
    async function* reader() {
      yield { turn_id: "turn-1", content: "Half" };
      await aborted;
    }
    api.streamChat.mockReturnValue({ reader: reader(), abort: () => abort() });
    api.stopTurn.mockResolvedValue({ stopping: true });
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("hi", "m");
    });
    await waitFor(() => expect(result.current.streamingContent).toBe("Half"));
    act(() => result.current.stop());
    await act(() => sending);

    expect(api.stopTurn).toHaveBeenCalledWith(SID, "turn-1");
    expect(result.current.messages.at(-1)).toMatchObject({ id: "turn-1", content: "Half", status: "stopped" });
  });

  it("reads the reply again when the connection went but the reply goes on", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "turn-1", content: "Hel" }], new TypeError("network error")));
    api.listMessages
      .mockResolvedValueOnce([message("u1", "user", "hi"), message("turn-1", "assistant", "Hel", { status: "running" })])
      .mockResolvedValueOnce([message("u1", "user", "hi"), message("turn-1", "assistant", "Hello")]);
    api.resumeStream.mockReturnValue(stream([{ content: "Hello" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("hi", "m"));

    expect(api.resumeStream).toHaveBeenCalledWith(SID);
    expect(result.current.error).toBeNull();
    expect(result.current.messages.map((m) => [m.id, m.content])).toEqual([["u1", "hi"], ["turn-1", "Hello"]]);
  });

  it("keeps what the user wrote when the send was refused before it was kept", async () => {
    const refused = "Replies are being written in 3 of your chats. Wait for one to finish.";
    api.streamChat.mockReturnValue(stream([], new Error(refused)));
    api.listMessages.mockResolvedValue([message("u0", "user", "earlier"), message("t0", "assistant", "answer")]);
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("my new question", "m"));

    expect(result.current.error).toBe(refused);
    expect(result.current.messages.at(-1)).toMatchObject({ role: "user", content: "my new question" });
  });

  it("says so when the model ended without a word, and keeps the message to send again", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "turn-1", reasoning: "Let me think." }, { finish_reason: "stop" }]));
    api.listMessages.mockResolvedValue([message("u0", "user", "earlier"), message("t0", "assistant", "answer")]);
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("my question", "m"));

    expect(result.current.error).toMatch(/No response received/);
    expect(result.current.messages.at(-1)).toMatchObject({ role: "user", content: "my question" });
  });

  it("says that the model ran out of length when it thought until the limit", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "turn-1", reasoning: "Thinking..." }, { finish_reason: "length" }]));
    api.listMessages.mockResolvedValue([message("u0", "user", "earlier"), message("t0", "assistant", "answer")]);
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("my question", "m"));

    expect(result.current.error).toMatch(/length limit/);
  });

  it("stops a reply it came back to", async () => {
    let abort!: () => void;
    const aborted = new Promise<never>((_, reject) => {
      abort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    });
    async function* reader() {
      yield { content: "Hal" };
      await aborted;
    }
    api.listMessages.mockResolvedValue([message("u1", "user", "hi"), message("turn-7", "assistant", "", { status: "running" })]);
    api.resumeStream.mockReturnValue({ reader: reader(), abort: () => abort() });
    api.stopTurn.mockResolvedValue({ stopping: true });
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    let loading!: Promise<void>;
    act(() => {
      loading = result.current.loadHistory();
    });
    await waitFor(() => expect(result.current.streamingContent).toBe("Hal"));
    act(() => result.current.stop());
    await act(() => loading);

    expect(api.stopTurn).toHaveBeenCalledWith(SID, "turn-7");
  });

  it("keeps on the reply the pictures the gateway did not send", async () => {
    const leftOut = {
      faden: {
        type: "CUSTOM",
        name: "faden.attachment_left_out",
        value: {
          // The page says it in the user's language; the gateway's words are for other clients.
          attachmentId: "a1", filename: "cat.png", model: "text-only", reason: "no_vision", message: "(the gateway's words)",
        },
      },
    };
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    async function* reader() {
      yield { turn_id: "turn-1" };
      yield leftOut;
      yield { content: "I can't see it." };
      await held;
    }
    api.streamChat.mockReturnValue({ reader: reader(), abort: vi.fn() });
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "text-only" }));
    const note = "cat.png was not sent: text-only does not take pictures. Choose a model that does.";

    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("What is in it?", "text-only");
    });
    await waitFor(() => expect(result.current.streamingNotes).toEqual([note]));
    release();
    await act(() => sending);

    expect(result.current.streamingNotes).toEqual([]);
    expect(result.current.messages.at(-1)).toMatchObject({ id: "turn-1", content: "I can't see it." });
    expect(result.current.getReplyNotes("turn-1")).toEqual([note]);
    expect(result.current.getReplyNotes("u1")).toEqual([]);
  });

  it("shows a reply still being written as it streams, then as stored", async () => {
    const writing = message("turn-1", "assistant", "", { status: "running" });
    api.listMessages
      .mockResolvedValueOnce([message("u1", "user", "hi"), writing])
      .mockResolvedValueOnce([message("u1", "user", "hi"), message("turn-1", "assistant", "Hello")]);
    api.resumeStream.mockReturnValue(stream([{ content: "Hello" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.loadHistory());

    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.messages.map((m) => [m.id, m.content])).toEqual([["u1", "hi"], ["turn-1", "Hello"]]);
  });
});
