/**
 * A picture sent with a message stays on it, and a retried reply keeps the
 * note that the picture was left out.
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
  uploadAttachment: vi.fn(),
  streamChat: vi.fn(),
  retryTurn: vi.fn(),
  resumeStream: vi.fn(),
}));

vi.mock("~/api/chatClient", () => ({
  ChatApiError: class ChatApiError extends Error {},
  chatApi: {
    listMessages: api.listMessages,
    streamStatus: api.streamStatus,
    getSession: api.getSession,
    stopTurn: api.stopTurn,
    uploadAttachment: api.uploadAttachment,
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
    provider_instance_id: null, token_estimate: null, trace_id: null, created_at: "2026-11-20T12:00:00Z",
    status: "complete", ...extra,
  };
}

function stream(deltas: StreamDelta[], error?: Error): StreamHandle {
  async function* reader() {
    for (const delta of deltas) yield delta;
    if (error) throw error;
  }
  return { reader: reader(), abort: vi.fn() };
}

const LEFT_OUT: StreamDelta = {
  faden: {
    type: "CUSTOM",
    name: "faden.attachment_left_out",
    value: { reason: "no_vision", filename: "cat.png", model: "m", message: "left out" },
  },
};
const NOTE = "cat.png was not sent: m does not take pictures. Choose a model that does.";

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.streamStatus.mockResolvedValue({ active: false });
  api.getSession.mockResolvedValue({ id: SID });
  api.uploadAttachment.mockResolvedValue({});
  URL.createObjectURL = vi.fn(() => "blob:http://localhost/cat");
  URL.revokeObjectURL = vi.fn();
});

describe("a message sent with a picture", () => {
  it("holds the picture, as a thumbnail the chat can show", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1", content: "Nice cat" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("What is this?", "m", [new File(["x"], "cat.png", { type: "image/png" })]));

    const sent = result.current.messages.find((m) => m.role === "user");
    expect(sent?.content_parts).toEqual([
      { type: "image_url", image_url: { url: "blob:http://localhost/cat" }, filename: "cat.png" },
    ]);
  });

  it("holds no picture for a document", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1", content: "ok" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("Read this", "m", [new File(["x"], "a.pdf")]));

    expect(result.current.messages.find((m) => m.role === "user")?.content_parts).toBeNull();
  });

  it("keeps the picture when the chat is read again from the gateway", async () => {
    api.streamChat.mockReturnValue(stream([{ turn_id: "t1" }], new Error("boom")));
    api.listMessages.mockResolvedValue([
      message("u1", "user", "What is this?"),
      message("t1", "assistant", "", { status: "failed", error: "boom" }),
    ]);
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("What is this?", "m", [new File(["x"], "cat.png")]));

    const sent = result.current.messages.find((m) => m.role === "user");
    expect(sent?.id).toBe("u1");
    expect(sent?.content_parts).toEqual([
      { type: "image_url", image_url: { url: "blob:http://localhost/cat" }, filename: "cat.png" },
    ]);
  });
});

describe("a retried reply", () => {
  it("keeps the note that the picture was left out", async () => {
    const failed = message("turn-1", "assistant", "", { status: "failed", error: "boom" });
    api.streamChat.mockReturnValue(stream([{ turn_id: "turn-1" }, LEFT_OUT], new Error("boom")));
    api.listMessages.mockResolvedValue([message("u1", "user", "What is this?"), failed]);
    // The new reply does not say it again.
    api.retryTurn.mockReturnValue(stream([{ turn_id: "turn-2", content: "I cannot see it" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("What is this?", "m", [new File(["x"], "cat.png")]));
    expect(result.current.getReplyNotes("turn-1")).toEqual([NOTE]);

    await act(() => result.current.retry());

    expect(api.retryTurn).toHaveBeenCalled();
    expect(result.current.getReplyNotes("turn-2")).toEqual([NOTE]);
  });

  it("does not say it twice when the new reply says it again", async () => {
    const failed = message("turn-1", "assistant", "", { status: "failed", error: "boom" });
    api.streamChat.mockReturnValue(stream([{ turn_id: "turn-1" }, LEFT_OUT], new Error("boom")));
    api.listMessages.mockResolvedValue([message("u1", "user", "What is this?"), failed]);
    api.retryTurn.mockReturnValue(stream([{ turn_id: "turn-2" }, LEFT_OUT, { content: "I cannot see it" }]));
    const { result } = renderHook(() => useChatStream({ sessionId: SID, selectedModel: "m" }));

    await act(() => result.current.send("What is this?", "m", [new File(["x"], "cat.png")]));
    await act(() => result.current.retry());

    expect(result.current.getReplyNotes("turn-2")).toEqual([NOTE]);
  });
});
