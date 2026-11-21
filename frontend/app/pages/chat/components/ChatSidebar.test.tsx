/**
 * A long chat list renders a part at a time.
 *
 * With every chat rendered at once, 3,400 of them held the page for 17 s
 * before anything showed.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { ChatProject, ChatSession } from "~/api/chatTypes";
import ChatSidebar from "./ChatSidebar";

function sessions(n: number): ChatSession[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `s${i}`,
    tenant_id: "t",
    user_id: "u",
    project_id: null,
    title: `Chat ${i}`,
    status: "active",
    created_at: "2026-09-26T00:00:00Z",
    updated_at: "2026-09-26T00:00:00Z",
  }));
}

function renderList(
  list: ChatSession[],
  activeSessionId?: string,
  extra: { onDeleteSession?: (id: string) => void; onDeleteProject?: (id: string) => void; projects?: ChatProject[] } = {},
) {
  return render(
    <MemoryRouter>
      <ChatSidebar
        open
        onToggle={() => {}}
        sessions={list}
        projects={extra.projects ?? []}
        activeSessionId={activeSessionId}
        onNewChat={() => {}}
        onSelectSession={() => {}}
        onDeleteSession={extra.onDeleteSession ?? (() => {})}
        onCreateProject={vi.fn()}
        onDeleteProject={extra.onDeleteProject ?? (() => {})}
        onUpdateProject={vi.fn()}
        onMoveSession={() => {}}
        width={260}
        isMobile={false}
      />
    </MemoryRouter>,
  );
}

describe("a long chat list", () => {
  it("renders the first 50 and more on request", () => {
    renderList(sessions(120));

    expect(screen.getByText("Chat 49")).toBeInTheDocument();
    expect(screen.queryByText("Chat 50")).toBeNull();
    fireEvent.click(screen.getByText("Show more (70)"));

    expect(screen.getByText("Chat 99")).toBeInTheDocument();
    expect(screen.getByText("Show more (20)")).toBeInTheDocument();
  });

  it("always renders up to the open chat", () => {
    renderList(sessions(120), "s87");

    expect(screen.getByText("Chat 87")).toBeInTheDocument();
    expect(screen.queryByText("Chat 88")).toBeNull();
  });
});

describe("a chat a reply is being written in", () => {
  it("is marked, and the others are not", () => {
    const list = sessions(3).map((s) => (s.id === "s1" ? { ...s, writing: true } : s));
    renderList(list);

    const marks = screen.getAllByTestId("chat-writing");
    expect(marks).toHaveLength(1);
    expect(marks[0].closest("[role=button]")).toHaveTextContent("Chat 1");
  });
});

describe("deleting a chat or a project", () => {
  const project = { id: "p1", name: "Plans" } as ChatProject;

  it("asks first, and deletes the chat only on Delete", async () => {
    const onDeleteSession = vi.fn();
    renderList(sessions(2), undefined, { onDeleteSession });

    // No hover in jsdom: the chat's actions are behind the ⋮ button.
    const openDelete = () => {
      fireEvent.click(screen.getAllByRole("button", { name: "Chat actions" })[0]);
      fireEvent.click(within(screen.getByRole("menu")).getByText("Delete"));
    };
    openDelete();
    expect(onDeleteSession).not.toHaveBeenCalled();
    expect(screen.getByText("Delete this chat?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDeleteSession).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    openDelete();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(onDeleteSession).toHaveBeenCalledTimes(1);
  });

  it("says a project's chats go with it, and deletes it only on Delete", () => {
    const onDeleteProject = vi.fn();
    renderList([], undefined, { onDeleteProject, projects: [project] });

    fireEvent.click(screen.getByLabelText("Delete"));
    expect(onDeleteProject).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toHaveTextContent('The project "Plans" and all its chats will be deleted.');
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(onDeleteProject).toHaveBeenCalledWith("p1");
  });
});
