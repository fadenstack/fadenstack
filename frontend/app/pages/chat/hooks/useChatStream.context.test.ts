/**
 * The context budget: what ``faden.context`` says is kept, the line on older messages is said once
 * per chat, and a turn that overflows its context is tried again once with less.
 */
import { act, renderHook } from "@testing-library/react";
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
  uploadAttachment: vi.fn(),
}));

vi.mock("~/api/chatClient", () => ({
  BUDGET_EXCEEDED: "budget_exceeded",
  ChatApiError: class ChatApiError extends Error {
    code: string | null;
    type: string | null = null;
    status: number | null = null;
    turnId: string | null;
    constructor(message: string, init: { code?: string; turnId?: string } = {}) {
      super(message);
      this.code = init.code ?? null;
      this.turnId = init.turnId ?? null;
    }
  },
  chatApi: {
    listMessages: api.listMessages, streamStatus: api.streamStatus, getSession: api.getSession, stopTurn: api.stopTurn,
    uploadAttachment: api.uploadAttachment,
  },
  streamChat: api.streamChat,
  retryTurn: api.retryTurn,
  resumeStream: api.resumeStream,
}));

import { ChatApiError } from "~/api/chatClient";
import { readFadenEvent, useChatStream } from "./useChatStream";

const SID = "s1";

function stream(deltas: StreamDelta[], error?: Error): StreamHandle {
  async function* reader() {
    for (const delta of deltas) yield delta;
    if (error) throw error;
  }
  return { reader: reader(), abort: vi.fn() };
}

function message(id: string, role: ChatMessage["role"], content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id, session_id: SID, role, content, content_parts: null, tool_call_json: null, model_alias: "m",
    provider_instance_id: null, token_estimate: null, trace_id: null, created_at: "2026-09-30T12:00:00Z",
    status: "complete", ...extra,
  };
}

function contextEvent(value: Partial<Record<string, unknown>>): StreamDelta {
  return {
    faden: {
      type: "CUSTOM",
      name: "faden.context",
      value: { tokens: 12300, budget: 25800, window: 32768, dropped: 0, summary: false, summarised: false, ...value },
    },
  };
}

const OVERFLOW = () => new ChatApiError("Too long", { code: "context_length_exceeded" } as never);
const FAILED = message("t1", "assistant", "", { status: "failed", error: "Too long" });

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.streamStatus.mockResolvedValue({ active: false });
  api.getSession.mockResolvedValue({ id: SID });
});

describe("faden.context", () => {
  it("reads the event's numbers", () => {
    expect(
      readFadenEvent({ type: "CUSTOM", name: "faden.context", value: { tokens: 5, budget: 9, window: null, dropped: 2, summary: true, summarised: false } }),
    ).toEqual({ kind: "context", context: { tokens: 5, budget: 9, window: null, dropped: 2, summary: true, summarised: false } });
  });

  it("keeps the last turn's context", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1" }, contextEvent({}), { content: "Hi" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));
    await act(() => result.current.send("hello", "m"));
    expect(result.current.context).toMatchObject({ tokens: 12300, budget: 25800, window: 32768 });
    expect(result.current.olderNote).toBeNull();
  });

  it("says that older messages are not sent once per chat, where they stopped", async () => {
    api.streamChat
      .mockReturnValueOnce(stream([{ turn_id: "t1" }, contextEvent({ dropped: 3 }), { content: "One" }]))
      .mockReturnValueOnce(stream([{ turn_id: "t2" }, contextEvent({ dropped: 5 }), { content: "Two" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));
    await act(() => result.current.send("first", "m"));
    expect(result.current.olderNote).toEqual({ summary: false, index: 0 });

    await act(() => result.current.send("second", "m"));
    // The same line, where it first stood: not a second one.
    expect(result.current.olderNote).toEqual({ summary: false, index: 0 });
  });

  it("words the line for a summary once one stands for the older messages", async () => {
    api.streamChat
      .mockReturnValueOnce(stream([{ turn_id: "t1" }, contextEvent({ dropped: 3 }), { content: "One" }]))
      .mockReturnValueOnce(stream([{ turn_id: "t2" }, contextEvent({ dropped: 4, summary: true, summarised: true }), { content: "Two" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));
    await act(() => result.current.send("first", "m"));
    await act(() => result.current.send("second", "m"));
    expect(result.current.olderNote).toEqual({ summary: true, index: 0 });
  });
});

describe("a context that was too long", () => {
  it("is tried again once with less, without asking", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1" }], OVERFLOW()));
    api.listMessages.mockResolvedValue([message("u1", "user", "hello"), FAILED]);
    api.retryTurn.mockReturnValue(stream([{ turn_id: "t2", content: "Hi" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("hello", "m", undefined, ["tone"], "low"));

    expect(api.retryTurn).toHaveBeenCalledTimes(1);
    expect(api.retryTurn).toHaveBeenCalledWith(SID, "t1", {
      context_factor: 0.75, model: "m", skills: ["tone"], reasoning_effort: "low",
    });
    expect(result.current.error).toBeNull();
    expect(result.current.messages.at(-1)).toMatchObject({ id: "t2", content: "Hi" });
  });

  it("is never tried a second time: the failure shows when the retry fails too", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1" }], OVERFLOW()));
    api.listMessages.mockResolvedValue([message("u1", "user", "hello"), FAILED]);
    api.retryTurn.mockReturnValue(stream([{ turn_id: "t1" }], OVERFLOW()));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("hello", "m"));

    expect(api.retryTurn).toHaveBeenCalledTimes(1);
    expect(result.current.messages.at(-1)).toMatchObject({ id: "t1", status: "failed" });
  });

  it("is tried again for a refusal that came as a whole (HTTP 400), by the turn the response named", async () => {
    api.streamChat.mockReturnValue(stream([], new ChatApiError("Too long", { code: "context_length_exceeded", turnId: "t9" } as never)));
    api.listMessages.mockResolvedValue([message("u1", "user", "hello")]);
    api.retryTurn.mockReturnValue(stream([{ turn_id: "t9", content: "Hi" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("hello", "m"));

    expect(api.retryTurn).toHaveBeenCalledWith(SID, "t9", { context_factor: 0.75, model: "m" });
  });

  it("does not retry another failure", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1" }], new Error("boom")));
    api.listMessages.mockResolvedValue([message("u1", "user", "hello"), { ...FAILED, error: "boom" }]);
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));
    await act(() => result.current.send("hello", "m"));
    expect(api.retryTurn).not.toHaveBeenCalled();
  });
});
