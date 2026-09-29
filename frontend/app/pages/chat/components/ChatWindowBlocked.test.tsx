/** "Edit and resend" puts a refused prompt back in the box: the server never kept its text. */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: [
      {
        id: "m1", session_id: "s1", role: "user", content: "", content_parts: null, tool_call_json: null,
        model_alias: null, provider_instance_id: null, token_estimate: null, trace_id: null,
        created_at: "2026-09-29T10:00:00Z", status: "blocked_input", notice: "Project names stay out.",
      },
    ],
    streamingContent: "",
    streamingUsage: null,
    isStreaming: false,
    isLoading: false,
    error: "Project names stay out.",
    errorInfo: { kind: "blocked", message: "Project names stay out.", text: "the code is BLUEBIRD" },
    getResponseMs: () => null,
    send: vi.fn(),
    retry: vi.fn(),
    stop: vi.fn(),
    loadHistory: vi.fn(),
  }),
}));

import ChatWindow from "./ChatWindow";

describe("a refused prompt", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("shows the notice on its turn, no error box, and edit puts the text back", () => {
    render(
      <MemoryRouter>
        <ChatWindow
          sessionId="s1" selectedModel="m" onModelChange={() => {}} onSessionCreated={() => {}}
          onSessionUpdated={() => {}} onToggleSidebar={() => {}} sidebarOpen languages={[]} language="en"
          onLanguageChange={() => {}} isSuperuser={false} onToolsToggle={() => {}} toolsOpen={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Project names stay out.")).toBeTruthy();
    expect(screen.queryByText("Retry")).toBeNull();
    fireEvent.click(screen.getByText("Edit and resend"));
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(box.value).toBe("the code is BLUEBIRD");
  });
});
