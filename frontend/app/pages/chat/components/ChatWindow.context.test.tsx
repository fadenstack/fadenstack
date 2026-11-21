/**
 * The chat shows what the turn said about its context: the usage figure on the latest reply and,
 * once, the line that older messages are no longer sent to the model.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { ChatMessage, ContextUse } from "~/api/chatTypes";

const hook = vi.hoisted(() => ({
  messages: [] as ChatMessage[],
  context: null as ContextUse | null,
  olderNote: null as { summary: boolean; index: number } | null,
}));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: hook.messages,
    streamingContent: "",
    streamingUsage: null,
    isStreaming: false,
    isLoading: false,
    error: null,
    errorInfo: null,
    pendingApproval: null,
    answerApproval: vi.fn(),
    toolNotices: [],
    streamingNotes: [],
    getReplyNotes: () => [],
    getResponseMs: () => null,
    context: hook.context,
    olderNote: hook.olderNote,
    send: vi.fn(),
    retry: vi.fn(),
    stop: vi.fn(),
    loadHistory: vi.fn(),
  }),
}));

import ChatWindow from "./ChatWindow";

function message(id: string, role: ChatMessage["role"], content: string): ChatMessage {
  return {
    id, session_id: "s1", role, content, content_parts: null, tool_call_json: null, model_alias: "m",
    provider_instance_id: null, token_estimate: null, trace_id: null, created_at: "2026-09-30T12:00:00Z",
    status: "complete",
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

describe("ChatWindow context budget", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
    vi.spyOn(chatApi, "getSummary").mockResolvedValue(null);
    hook.messages = [message("u1", "user", "first"), message("a1", "assistant", "one"), message("u2", "user", "second"), message("a2", "assistant", "two")];
    hook.context = null;
    hook.olderNote = null;
  });

  it("shows the context's use on the latest reply, with the numbers in the reader's format", () => {
    hook.context = { tokens: 12300, budget: 25800, window: 32768, dropped: 0, summary: false, summarised: false };
    renderChat();
    const figures = screen.getAllByTestId("context-figure");
    expect(figures).toHaveLength(1);
    expect(figures[0]).toHaveTextContent("Context: 12.3K of 25.8K tokens");
  });

  it("shows nothing about the context when the turn did not say", () => {
    renderChat();
    expect(screen.queryByTestId("context-figure")).toBeNull();
    expect(screen.queryByTestId("older-messages-note")).toBeNull();
  });

  it("says once that older messages are not sent, above the message the turn answered", () => {
    hook.olderNote = { summary: false, index: 2 };
    renderChat();
    const note = screen.getByTestId("older-messages-note");
    expect(note).toHaveTextContent("Older messages are no longer sent to the model. It can search them when it needs them.");
    const box = screen.getByTestId("chat-messages");
    const order = within(box).getByText("second").compareDocumentPosition(note);
    expect(order & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(screen.getAllByTestId("older-messages-note")).toHaveLength(1);
  });

  it("words the line for a summary", () => {
    hook.olderNote = { summary: true, index: 2 };
    renderChat();
    expect(screen.getByTestId("older-messages-note")).toHaveTextContent(
      "Older messages are no longer sent to the model; a summary written by the model stands for them.",
    );
  });

  it("offers Show summary on the line when a summary stands for the older messages, and opens it", async () => {
    hook.olderNote = { summary: true, index: 2 };
    vi.spyOn(chatApi, "getSummary").mockResolvedValue({
      id: "sum1", session_id: "s1", summary_text: "They agreed on a date.", last_message_id: null,
      token_estimate: 10, created_at: "2026-11-20T09:30:00Z", model_alias: "qwen", record_id: null,
    });
    renderChat();
    const note = screen.getByTestId("older-messages-note");
    // The AI label stands beside what opens it.
    expect(within(note).getByTestId("summary-ai-label")).toHaveTextContent(
      "Summary written by AI. It can leave out or get wrong what was said.",
    );
    fireEvent.click(within(note).getByRole("button", { name: "Show summary" }));
    expect(await screen.findByTestId("summary-text")).toHaveTextContent("They agreed on a date.");
  });

  it("has no Show summary on the line without a summary", () => {
    hook.olderNote = { summary: false, index: 2 };
    renderChat();
    expect(within(screen.getByTestId("older-messages-note")).queryByRole("button")).toBeNull();
  });

  it("offers the summary from the chat's bar when the chat has one", async () => {
    vi.spyOn(chatApi, "getSummary").mockResolvedValue({
      id: "sum1", session_id: "s1", summary_text: "They agreed on a date.", last_message_id: null,
      token_estimate: 10, created_at: "2026-11-20T09:30:00Z", model_alias: "qwen", record_id: "rec",
    });
    renderChat();
    const button = await screen.findByTestId("show-summary");
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("summary-text")).toBeInTheDocument());
    expect(screen.getByTestId("summary-marked")).toBeInTheDocument();
  });

  it("has no summary button on the bar for a chat without one", async () => {
    renderChat();
    await waitFor(() => expect(chatApi.getSummary).toHaveBeenCalled());
    expect(screen.queryByTestId("show-summary")).toBeNull();
  });
});
