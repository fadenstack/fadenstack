/**
 * The chat on a touch screen and a phone.
 *
 * jsdom matches no media query: no hover (a touch screen), and not below the
 * phone breakpoint -- the phone navigation comes from the shell context.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { ChatSession } from "~/api/chatTypes";
import { ChatShellContext, type ChatShell } from "../ChatShellContext";
import ChatInput from "./ChatInput";
import ChatSidebar from "./ChatSidebar";

function session(id: string, title: string): ChatSession {
  return {
    id,
    tenant_id: "t",
    user_id: "u",
    project_id: null,
    title,
    status: "active",
    created_at: "2026-09-26T00:00:00Z",
    updated_at: "2026-09-26T00:00:00Z",
  };
}

describe("the chat on a touch screen", () => {
  it("offers a chat's actions behind a ⋮ button, since there is no hover to reveal them", () => {
    const onDelete = vi.fn();
    render(
      <MemoryRouter>
        <ChatSidebar
          open
          onToggle={() => {}}
          sessions={[session("s1", "Quarterly report"), session("s2", "Trip plan")]}
          projects={[]}
          onNewChat={() => {}}
          onSelectSession={() => {}}
          onDeleteSession={onDelete}
          onCreateProject={vi.fn()}
          onDeleteProject={() => {}}
          onUpdateProject={vi.fn()}
          onMoveSession={() => {}}
          width={260}
          isMobile={false}
        />
      </MemoryRouter>,
    );

    const actions = screen.getAllByRole("button", { name: "Chat actions" });
    expect(actions).toHaveLength(2);
    fireEvent.click(actions[1]);
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Move to project")).toBeInTheDocument();
    fireEvent.click(within(menu).getByText("Delete"));

    expect(onDelete).toHaveBeenCalledWith("s2");
  });
});

describe("the composer on a phone", () => {
  beforeEach(() => {
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("floats the menu button above it, and puts it away while the user types", async () => {
    const onOpen = vi.fn();
    const shell: ChatShell = {
      userEmail: "a@example.com",
      onProfile: () => {},
      onLogout: () => {},
      phoneNav: { hand: "left", open: false, onOpen },
    };
    render(
      <MemoryRouter>
        <ChatShellContext.Provider value={shell}>
          <ChatInput onSend={() => {}} selectedModel="m" onModelChange={() => {}} />
        </ChatShellContext.Provider>
      </MemoryRouter>,
    );

    const menu = screen.getByRole("button", { name: "Open navigation" });
    expect(menu.dataset.hand).toBe("left");
    fireEvent.click(menu);
    expect(onOpen).toHaveBeenCalledOnce();

    fireEvent.focus(screen.getByPlaceholderText("Type a message…"));
    await waitFor(() => expect(menu).toHaveStyle({ visibility: "hidden" }));
  });
});
