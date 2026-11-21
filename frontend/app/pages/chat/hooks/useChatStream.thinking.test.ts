/**
 * The thinking level goes on the turn's body as ``reasoning_effort`` (nothing for Auto), and on the
 * retry's body with the same level.
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
  ChatApiError: class ChatApiError extends Error {},
  chatApi: {
    listMessages: api.listMessages, streamStatus: api.streamStatus, getSession: api.getSession, stopTurn: api.stopTurn,
    uploadAttachment: api.uploadAttachment,
  },
  streamChat: api.streamChat,
  retryTurn: api.retryTurn,
  resumeStream: api.resumeStream,
}));

import { useChatStream } from "./useChatStream";

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

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.streamStatus.mockResolvedValue({ active: false });
  api.getSession.mockResolvedValue({ id: SID });
});

describe("useChatStream thinking level", () => {
  it.each([
    ["none", { reasoning_effort: "none" }],
    ["high", { reasoning_effort: "high" }],
    [undefined, {}],
  ])("sends %s on the turn", async (effort, extra) => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1", content: "Hi" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));
    await act(() => result.current.send("hello", "m", undefined, undefined, effort));
    expect(api.streamChat).toHaveBeenCalledWith({
      session_id: SID,
      model: "m",
      messages: [{ role: "user", content: "hello" }],
      ...extra,
    });
  });

  it("sends the same level again when the failed reply is retried", async () => {
    const failed = message("t1", "assistant", "", { status: "failed", error: "x" });
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1" }], new Error("x")));
    api.listMessages.mockResolvedValue([message("u1", "user", "hello"), failed]);
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));
    await act(() => result.current.send("hello", "m", undefined, undefined, "low"));

    api.retryTurn.mockReturnValue(stream([{ turn_id: "t2", content: "Hi" }]));
    await act(() => result.current.retry());
    expect(api.retryTurn).toHaveBeenCalledWith(SID, "t1", { model: "m", reasoning_effort: "low" });
  });
});
