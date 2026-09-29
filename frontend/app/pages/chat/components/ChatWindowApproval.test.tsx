/** The approval card: the answer waits until the user decides. */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";

const answerApproval = vi.fn();

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: [], streamingContent: "Let me check.", streamingUsage: null, isStreaming: true, isLoading: false,
    error: null, errorInfo: null, toolNotices: ["mcp.gh.delete_repo: Only read-only tools are allowed in this chat."],
    pendingApproval: { approvalId: "a1", toolName: "mcp.gh.create_issue", message: "Needs your OK.", timeoutS: 300 },
    answerApproval, getResponseMs: () => null, send: vi.fn(), retry: vi.fn(), stop: vi.fn(), loadHistory: vi.fn(),
  }),
}));

import ChatWindow from "./ChatWindow";

describe("a tool waiting for approval", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
    answerApproval.mockReset();
  });

  it("asks, and the answer goes to the stream", () => {
    render(
      <MemoryRouter>
        <ChatWindow
          sessionId="s1" selectedModel="m" onModelChange={() => {}} onSessionCreated={() => {}}
          onSessionUpdated={() => {}} onToggleSidebar={() => {}} sidebarOpen languages={[]} language="en"
          onLanguageChange={() => {}} isSuperuser={false} onToolsToggle={() => {}} toolsOpen={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("mcp.gh.create_issue wants to run")).toBeTruthy();
    expect(screen.getByText("Needs your OK.")).toBeTruthy();
    expect(screen.getByTestId("tool-notices").textContent).toContain("mcp.gh.delete_repo");
    fireEvent.click(screen.getByText("Approve"));
    expect(answerApproval).toHaveBeenCalledWith(true);
  });
});
