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

const hook = vi.hoisted(() => ({ messages: [] as ChatMessage[], retry: vi.fn() }));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: hook.messages,
    streamingContent: "",
    streamingUsage: null,
    isStreaming: false,
    isLoading: false,
    error: null,
    getResponseMs: () => null,
    send: vi.fn(),
    retry: hook.retry,
    stop: vi.fn(),
    loadHistory: vi.fn(),
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

function renderChat() {
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

  it("says a reply was cut off by a restart", () => {
    hook.messages = [message("u1", "user", "hi"), message("t1", "assistant", "", { status: "interrupted" })];
    renderChat();
    expect(screen.getByTestId("failed-reply")).toHaveTextContent(
      "The server restarted while this reply was being written.",
    );
  });
});
