/**
 * A request a budget refused, on its turn: labelled as a budget (not a rate limit), worded in the reader's
 * language from the code and the kind of budget the gateway names (`error.param`) -- never from its English
 * message, and with no figures -- whom to ask, and when it starts again: a date, not a count of seconds.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { ChatErrorDetail } from "../hooks/useChatStream";

/** The gateway's own words, which the chat does not show. */
const GATEWAY_MESSAGE = "Budget 'Sales' for 2026-10: 999,000 of 1,000,000 tokens used.";

const state = vi.hoisted(() => ({ errorInfo: null as ChatErrorDetail | null }));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: [],
    streamingContent: "",
    streamingUsage: null,
    isStreaming: false,
    isLoading: false,
    error: state.errorInfo?.message ?? null,
    errorInfo: state.errorInfo,
    getResponseMs: () => null,
    send: vi.fn(),
    retry: vi.fn(),
    stop: vi.fn(),
    loadHistory: vi.fn(),
  }),
}));

import ChatWindow from "./ChatWindow";

function view() {
  render(
    <MemoryRouter>
      <ChatWindow
        sessionId="s1" selectedModel="m" onModelChange={() => {}} onSessionCreated={() => {}}
        onSessionUpdated={() => {}} onToggleSidebar={() => {}} sidebarOpen languages={[]} language="en"
        onLanguageChange={() => {}} isSuperuser={false} onToolsToggle={() => {}} toolsOpen={false}
      />
    </MemoryRouter>,
  );
  return screen.getByTestId("failed-reply");
}

describe("a request refused by a budget", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("names the kind of budget, whom to ask, and when it starts again", () => {
    state.errorInfo = { kind: "budget", message: GATEWAY_MESSAGE, retryAfter: 3 * 86400, param: "team" };
    const reply = view();

    expect(reply).toHaveTextContent("Budget used up");
    expect(reply).not.toHaveTextContent("Rate limit reached");
    expect(reply).toHaveTextContent(
      "This would go over your team's budget for this period. Ask an administrator or the budget's owner for more.",
    );
    expect(reply).toHaveTextContent(/It starts again on .+\d{4}/);
    // Neither the gateway's English message nor any figure.
    expect(reply).not.toHaveTextContent("999,000");
    expect(reply).not.toHaveTextContent("259200");
  });

  it.each([
    ["personal", "your personal budget"],
    ["agent", "this agent's budget"],
    ["organisation", "the organisation's budget"],
    [null, "a budget for this period"],
  ])("words the kind %s", (param, words) => {
    state.errorInfo = { kind: "budget", message: GATEWAY_MESSAGE, retryAfter: null, param };
    const reply = view();

    expect(reply).toHaveTextContent(words);
    expect(reply).not.toHaveTextContent("It starts again");
  });
});
