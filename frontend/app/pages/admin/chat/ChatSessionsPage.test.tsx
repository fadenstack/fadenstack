/**
 * The admin chat list shows chats by id, never by title.
 *
 * A chat's title is the start of its user's first message, theirs as the
 * messages are; it was listed in plain text on every install (2026-10-02).
 */
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatAdmin, type ChatSession } from "~/api/chat";
import { renderPage } from "~/test/renderPage";

import ChatSessionsPage from "./ChatSessionsPage";

const CHAT = {
  id: "3f2a9c1e-7b44-4d0a-9e51-0c6d2b8f1a77",
  tenant_id: "t",
  user_id: "user-1",
  project_id: null,
  status: "active",
  created_at: "2026-10-02T08:00:00Z",
  updated_at: "2026-10-02T08:05:00Z",
} as unknown as ChatSession;

describe("ChatSessionsPage", () => {
  beforeEach(() => {
    vi.spyOn(chatAdmin, "listSessions").mockResolvedValue([CHAT]);
  });

  it("lists a chat by its id, with no title column", async () => {
    renderPage(<ChatSessionsPage />);
    expect(await screen.findByTestId("chat-id")).toHaveTextContent("3f2a9c1e");
    expect(screen.getByText("Chat")).toBeInTheDocument();
    expect(screen.queryByText("Title")).not.toBeInTheDocument();
  });
});
