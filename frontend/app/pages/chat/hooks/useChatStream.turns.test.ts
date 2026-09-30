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
  streamChat: vi.fn(),
  retryTurn: vi.fn(),
  resumeStream: vi.fn(),
}));

vi.mock("~/api/chatClient", () => ({
  chatApi: { listMessages: api.listMessages, streamStatus: api.streamStatus, getSession: api.getSession },
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
