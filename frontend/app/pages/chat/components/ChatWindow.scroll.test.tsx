/**
 * While a reply is written the view follows it -- but not against the user.
 *
 * Every token used to scroll the window to the bottom, so a user who scrolled
 * up to read an earlier answer (or the thinking) was pulled straight back down
 * and could not read anything while a reply was being written. When to let go
 * is followScroll's (followScroll.test.ts); this is the window around it.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";

const stream = {
  messages: [] as unknown[],
  streamingContent: "",
  streamingReasoning: "",
  streamingUsage: null,
  isStreaming: true,
  isLoading: false,
  error: null,
  toolNotices: [] as string[],
  pendingApproval: null,
  getResponseMs: () => null,
  send: vi.fn(),
  retry: vi.fn(),
  stop: vi.fn(),
  loadHistory: vi.fn(),
};

vi.mock("../hooks/useChatStream", () => ({ useChatStream: () => ({ ...stream }) }));

import ChatWindow from "./ChatWindow";

function view() {
  return (
    <MemoryRouter initialEntries={["/chat/s1"]}>
      <ChatWindow
        sessionId="s1"
        selectedModel="m"
        onModelChange={() => {}}
        onSessionCreated={() => {}}
        onSessionUpdated={() => {}}
        onToggleSidebar={() => {}}
        sidebarOpen
        languages={[]}
        language="en"
        onLanguageChange={() => {}}
        isSuperuser={false}
        onToolsToggle={() => {}}
        toolsOpen={false}
      />
    </MemoryRouter>
  );
}

/** jsdom has no layout: give the message area a size to scroll in. */
function sized(el: HTMLElement, scrollHeight: number) {
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, value: 400 });
}

/** The reader turns the wheel up a little, as a trackpad does. */
function scrollUp(box: HTMLElement, to: number) {
  fireEvent.wheel(box, { deltaY: -8 });
  box.scrollTop = to;
  fireEvent.scroll(box);
}

describe("ChatWindow scrolling while a reply is written", () => {
  beforeEach(() => {
    stream.streamingContent = "";
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("follows new tokens while the user is at the bottom", () => {
    const { rerender } = render(view());
    const box = screen.getByTestId("chat-messages");
    sized(box, 1000);
    stream.streamingContent = "Hello";
    rerender(view());
    expect(box.scrollTop).toBe(1000);
    expect(screen.queryByTestId("jump-to-latest")).toBeNull();
  });

  it("stays where the user scrolled to, and offers the way back down", () => {
    const { rerender } = render(view());
    const box = screen.getByTestId("chat-messages");
    sized(box, 1000);
    stream.streamingContent = "Hello";
    rerender(view());
    scrollUp(box, 990);

    stream.streamingContent = "Hello, a longer reply";
    sized(box, 1400);
    rerender(view());
    expect(box.scrollTop).toBe(990);

    act(() => screen.getByTestId("jump-to-latest").click());
    expect(box.scrollTop).toBe(1400);

    // Following again: the next tokens are followed.
    sized(box, 1800);
    stream.streamingContent = "Hello, a longer reply still";
    rerender(view());
    expect(box.scrollTop).toBe(1800);
  });

  it("follows again once the user scrolls back to the bottom", () => {
    const { rerender } = render(view());
    const box = screen.getByTestId("chat-messages");
    sized(box, 1000);
    stream.streamingContent = "Hello";
    rerender(view());
    scrollUp(box, 100);
    expect(screen.getByTestId("jump-to-latest")).toBeTruthy();

    box.scrollTop = 600; // 1000 - 600 - 400 = 0 left below
    fireEvent.scroll(box);
    expect(screen.queryByTestId("jump-to-latest")).toBeNull();

    sized(box, 1300);
    stream.streamingContent = "More";
    rerender(view());
    expect(box.scrollTop).toBe(1300);
  });
});
