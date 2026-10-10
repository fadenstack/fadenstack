/**
 * A stored reply that failed shows why, and the chat's last one has Retry;
 * a stopped reply says it was stopped.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { ChatMessage } from "~/api/chatTypes";

const hook = vi.hoisted(() => ({
  messages: [] as ChatMessage[], retry: vi.fn(), loadHistory: vi.fn(), isStreaming: false,
  streamingContent: "", streamingNotes: [] as string[], replyNotes: {} as Record<string, string[]>,
}));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: hook.messages,
    streamingContent: hook.streamingContent,
    streamingUsage: null,
    isStreaming: hook.isStreaming,
    isLoading: false,
    error: null,
    errorInfo: null,
    pendingApproval: null,
    answerApproval: vi.fn(),
    toolNotices: [],
    streamingNotes: hook.streamingNotes,
    getReplyNotes: (id: string) => hook.replyNotes[id] ?? [],
    getResponseMs: () => null,
    send: vi.fn(),
    retry: hook.retry,
    stop: vi.fn(),
    loadHistory: hook.loadHistory,
  }),
}));

import ChatWindow from "./ChatWindow";

function message(id: string, role: ChatMessage["role"], content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id, session_id: "s1", role, content, content_parts: null, tool_call_json: null, model_alias: "m",
    provider_instance_id: null, token_estimate: null, trace_id: null, created_at: "2026-09-30T12:00:00Z",
    status: "complete", ...extra,
  };
}

function renderChat(onWritingChange?: (sessionId: string, writing: boolean) => void) {
  render(
    <MemoryRouter initialEntries={["/chat/s1"]}>
      <Routes>
        <Route
          path="/chat/:sessionId?"
          element={
            <ChatWindow
              sessionId="s1" selectedModel="m" onModelChange={() => {}} onSessionCreated={() => {}}
              onSessionUpdated={() => {}} onToggleSidebar={() => {}} sidebarOpen languages={[]} language="en"
              onLanguageChange={() => {}} isSuperuser={false} onToolsToggle={() => {}} toolsOpen={false}
              onWritingChange={onWritingChange}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ChatWindow's stored replies", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    hook.retry.mockReset();
    hook.loadHistory.mockReset();
    hook.isStreaming = false;
    hook.replyNotes = {};
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("shows why a reply failed and tries the last one again", async () => {
    hook.messages = [
      message("u1", "user", "hi"),
      message("t1", "assistant", "", { status: "failed", error: "Model is not reachable" }),
      message("u2", "user", "again"),
      message("t2", "assistant", "", { status: "failed", error: "System message must be at the beginning." }),
    ];
    renderChat();

    const [first, last] = screen.getAllByTestId("failed-reply");
    expect(first).toHaveTextContent("Model is not reachable");
    expect(within(first).queryByRole("button", { name: "Retry" })).toBeNull();
    expect(last).toHaveTextContent("System message must be at the beginning.");
    await userEvent.click(within(last).getByRole("button", { name: "Retry" }));
    expect(hook.retry).toHaveBeenCalledTimes(1);
  });

  it("says a reply was stopped, and keeps what it wrote", () => {
    hook.messages = [message("u1", "user", "hi"), message("t1", "assistant", "Half an answer", { status: "stopped" })];
    renderChat();
    expect(screen.getByText("Half an answer")).toBeInTheDocument();
    expect(screen.getByTestId("reply-stopped")).toHaveTextContent("Stopped");
  });

  it("says on a reply which pictures were not sent with it", () => {
    const note = "cat.png was not sent: text-only does not take pictures. Choose a model that does.";
    hook.messages = [message("u1", "user", "What is in it?"), message("t1", "assistant", "I can't see it.")];
    hook.replyNotes = { t1: [note] };
    renderChat();
    const [notes] = screen.getAllByTestId("reply-notes");
    expect(notes).toHaveTextContent(note);
    expect(notes.closest("[data-message-role]")).toHaveAttribute("data-message-role", "assistant");
  });

  it("says a reply was cut off by a restart", () => {
    hook.messages = [message("u1", "user", "hi"), message("t1", "assistant", "", { status: "interrupted" })];
    renderChat();
    expect(screen.getByTestId("failed-reply")).toHaveTextContent(
      "The server restarted while this reply was being written.",
    );
  });
});

describe("ChatWindow while a reply is written", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    hook.loadHistory.mockReset();
    hook.isStreaming = false;
    hook.streamingContent = "";
    hook.streamingNotes = [];
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("says it on the reply as it streams", () => {
    const note = "cat.png was not sent: text-only does not take pictures. Choose a model that does.";
    hook.messages = [message("u1", "user", "What is in it?")];
    hook.isStreaming = true;
    hook.streamingContent = "I can't";
    hook.streamingNotes = [note];
    renderChat();
    expect(screen.getByTestId("reply-notes")).toHaveTextContent(note);
  });

  it("tells the chat list, which marks the chat", () => {
    hook.messages = [message("u1", "user", "hi")];
    hook.isStreaming = true;
    const onWritingChange = vi.fn();
    renderChat(onWritingChange);
    expect(onWritingChange).toHaveBeenCalledWith("s1", true);
  });

  it("reads a reply left running again when the tab is back", () => {
    hook.messages = [message("u1", "user", "hi"), message("t1", "assistant", "Hal", { status: "running" })];
    renderChat();
    hook.loadHistory.mockClear();

    document.dispatchEvent(new Event("visibilitychange"));
    expect(hook.loadHistory).toHaveBeenCalledTimes(1);
  });

  it("leaves a finished chat alone when the tab is back", () => {
    hook.messages = [message("u1", "user", "hi"), message("t1", "assistant", "Hello")];
    renderChat();
    hook.loadHistory.mockClear();

    document.dispatchEvent(new Event("visibilitychange"));
    expect(hook.loadHistory).not.toHaveBeenCalled();
  });
});

