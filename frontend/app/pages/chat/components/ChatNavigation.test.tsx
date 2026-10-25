/**
 * The chat's navigation: Projects and Chats as separate sections, chats
 * grouped by when they were last used, a title search, and renaming the
 * open chat from its bar.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { ChatProject, ChatSession } from "~/api/chatTypes";
import ChatSidebar, { dateGroupOf } from "./ChatSidebar";
import ChatTopbar from "./ChatTopbar";

const NOW = new Date();
// Calendar days, not 24-hour steps: on the day the clocks change, 24 hours back can still be today.
const daysAgo = (n: number) =>
  new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate() - n, NOW.getHours(), NOW.getMinutes()).toISOString();

function chat(id: string, title: string | null, projectId: string | null, updated = daysAgo(0)): ChatSession {
  return {
    id,
    tenant_id: "t",
    user_id: "u",
    project_id: projectId,
    title,
    status: "active",
    created_at: updated,
    updated_at: updated,
  };
}

const PROJECT: ChatProject = {
  id: "p1",
  tenant_id: "t",
  user_id: "u",
  name: "Launch",
  description: null,
  system_instructions: null,
  created_at: daysAgo(3),
  updated_at: daysAgo(3),
} as ChatProject;

function renderSidebar(list: ChatSession[]) {
  return render(
    <MemoryRouter>
      <ChatSidebar
        open
        onToggle={() => {}}
        sessions={list}
        projects={[PROJECT]}
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

const LIST = [
  chat("a", "Launch timeline", "p1"),
  chat("b", "Quarterly report", null),
  chat("c", "Launch party menu", null, daysAgo(1)),
  chat("d", "Tax questions", null, daysAgo(40)),
];

describe("the chat navigation", () => {
  it("keeps projects and chats apart, and groups the chats by when they were used", () => {
    renderSidebar(LIST);

    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.getByText("Chats")).toBeInTheDocument();
    expect(screen.getByText("Launch")).toBeInTheDocument(); // the project folder
    expect(screen.getByText("Launch timeline")).toBeInTheDocument(); // in it
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByText("Yesterday")).toBeInTheDocument();
    expect(screen.getByText("Older")).toBeInTheDocument();
  });

  it("searches titles, naming the project a match is in", () => {
    renderSidebar(LIST);
    fireEvent.change(screen.getByRole("textbox", { name: "Search chats" }), { target: { value: "launch" } });

    expect(screen.getByText("Results: 2")).toBeInTheDocument();
    const timeline = screen.getByText("Launch timeline").closest("[role='button'], a, div") as HTMLElement;
    expect(within(timeline.parentElement!).getByText("Launch")).toBeInTheDocument();
    expect(screen.getByText("Launch party menu")).toBeInTheDocument();
    expect(screen.queryByText("Quarterly report")).toBeNull();
    expect(screen.queryByText("Projects")).toBeNull();

    fireEvent.change(screen.getByRole("textbox", { name: "Search chats" }), { target: { value: "zzz" } });
    expect(screen.getByText("No chats match")).toBeInTheDocument();
  });

  it("dates a chat by the local day it was last used", () => {
    const now = new Date(2026, 8, 26, 9, 0);
    expect(dateGroupOf(new Date(2026, 8, 26, 0, 5).toISOString(), now)).toBe("today");
    expect(dateGroupOf(new Date(2026, 8, 25, 23, 50).toISOString(), now)).toBe("yesterday");
    expect(dateGroupOf(new Date(2026, 8, 21).toISOString(), now)).toBe("week");
    expect(dateGroupOf(new Date(2026, 8, 1).toISOString(), now)).toBe("month");
    expect(dateGroupOf(new Date(2026, 5, 1).toISOString(), now)).toBe("older");
  });
});

describe("the open chat's bar", () => {
  it("shows the chat's title and renames it in place", async () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <ChatTopbar
          title="Launch timeline"
          onRename={onRename}
          onToggleSidebar={() => {}}
          sidebarOpen
          languages={[]}
          language="en"
          onLanguageChange={() => {}}
          isSuperuser={false}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("Launch timeline")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Chat title" });
    fireEvent.change(input, { target: { value: "  Launch plan v2  " } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(onRename).toHaveBeenCalledWith("Launch plan v2"));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Chat title" })).toBeNull());
  });
});
