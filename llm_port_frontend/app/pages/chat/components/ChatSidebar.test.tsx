/**
 * A long chat list renders a part at a time.
 *
 * With every chat rendered at once, 3,400 of them held the page for 17 s
 * before anything showed.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { ChatSession } from "~/api/chatTypes";
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

function renderList(list: ChatSession[], activeSessionId?: string) {
  return render(
    <MemoryRouter>
      <ChatSidebar
        open
        onToggle={() => {}}
        sessions={list}
        projects={[]}
        activeSessionId={activeSessionId}
        onNewChat={() => {}}
        onSelectSession={() => {}}
        onDeleteSession={() => {}}
        onCreateProject={vi.fn()}
        onDeleteProject={() => {}}
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
