/** The approval card: the answer waits until the user decides. */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";

const answerApproval = vi.fn();
const state = vi.hoisted(() => ({ args: "" as string, truncated: false }));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: [], streamingContent: "Let me check.", streamingUsage: null, isStreaming: true, isLoading: false,
    error: null, errorInfo: null, toolNotices: ["mcp.gh.delete_repo: Only read-only tools are allowed in this chat."],
    pendingApproval: { approvalId: "a1", toolName: "mcp.gh.create_issue", message: "Needs your OK.", timeoutS: 300, arguments: state.args, argumentsTruncated: state.truncated, argumentsSha256: "" },
    answerApproval, getResponseMs: () => null, send: vi.fn(), retry: vi.fn(), stop: vi.fn(), loadHistory: vi.fn(),
  }),
}));

import ChatWindow from "./ChatWindow";

describe("a tool waiting for approval", () => {
  beforeEach(() => {
    state.args = "";
    state.truncated = false;
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
    answerApproval.mockReset();
  });

  const view = () =>
    render(
      <MemoryRouter>
        <ChatWindow
          sessionId="s1" selectedModel="m" onModelChange={() => {}} onSessionCreated={() => {}}
          onSessionUpdated={() => {}} onToggleSidebar={() => {}} sidebarOpen languages={[]} language="en"
          onLanguageChange={() => {}} isSuperuser={false} onToolsToggle={() => {}} toolsOpen={false}
        />
      </MemoryRouter>,
    );

  it("shows the arguments, indented when they are JSON, in a box that scrolls", () => {
    state.args = '{"title":"Bug","labels":["a"]}';
    view();
    const box = screen.getByTestId("tool-approval-arguments");
    expect(box.textContent).toBe(JSON.stringify({ title: "Bug", labels: ["a"] }, null, 2));
    expect(box).toHaveStyle({ "max-height": "200px", overflow: "auto" });
    expect(screen.queryByText(/Cut off/)).toBeNull();
  });

  it("shows arguments that are not JSON as they are, and says when they were cut", () => {
    state.args = '{"title": "Bu';
    state.truncated = true;
    view();
    expect(screen.getByTestId("tool-approval-arguments").textContent).toBe('{"title": "Bu');
    expect(screen.getByText("Cut off: only the first part of the arguments is shown.")).toBeTruthy();
  });

  it("asks, and the answer goes to the stream", () => {
    view();
    expect(screen.getByText("mcp.gh.create_issue wants to run")).toBeTruthy();
    expect(screen.getByText("Needs your OK.")).toBeTruthy();
    expect(screen.getByTestId("tool-notices").textContent).toContain("mcp.gh.delete_repo");
    fireEvent.click(screen.getByText("Approve"));
    expect(answerApproval).toHaveBeenCalledWith(true);
  });
});
