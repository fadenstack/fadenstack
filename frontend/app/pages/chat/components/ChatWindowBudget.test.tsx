/**
 * A used-up budget on its turn: labelled as a budget (not a rate limit), the gateway's own words (which
 * name the kind of budget and whom to ask), and when it starts again -- a date, not a count of seconds.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";

const MESSAGE = "Your team's budget for this month is used up. Ask an administrator or the budget's owner for more.";

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: [],
    streamingContent: "",
    streamingUsage: null,
    isStreaming: false,
    isLoading: false,
    error: MESSAGE,
    errorInfo: { kind: "budget", message: MESSAGE, retryAfter: 3 * 86400 },
    getResponseMs: () => null,
    send: vi.fn(),
    retry: vi.fn(),
    stop: vi.fn(),
    loadHistory: vi.fn(),
  }),
}));

import ChatWindow from "./ChatWindow";

describe("a refused request: the budget is used up", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("says so, names whom to ask, and when it starts again", () => {
    render(
      <MemoryRouter>
        <ChatWindow
          sessionId="s1" selectedModel="m" onModelChange={() => {}} onSessionCreated={() => {}}
          onSessionUpdated={() => {}} onToggleSidebar={() => {}} sidebarOpen languages={[]} language="en"
          onLanguageChange={() => {}} isSuperuser={false} onToolsToggle={() => {}} toolsOpen={false}
        />
      </MemoryRouter>,
    );
    const reply = screen.getByTestId("failed-reply");
    expect(reply).toHaveTextContent("Budget used up");
    expect(reply).not.toHaveTextContent("Rate limit reached");
    expect(reply).toHaveTextContent(MESSAGE);
    expect(reply).toHaveTextContent(/It starts again on .+\d{4}/);
    expect(reply).not.toHaveTextContent("259200");
  });
});
