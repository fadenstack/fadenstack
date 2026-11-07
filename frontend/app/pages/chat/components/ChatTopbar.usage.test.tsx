/** Everyone reaches their own usage page from the account menu (M03). */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { ChatShellContext, type ChatShell } from "../ChatShellContext";
import ChatTopbar from "./ChatTopbar";

describe("the account menu", () => {
  it("opens My usage", () => {
    const onUsage = vi.fn();
    const shell: ChatShell = {
      userEmail: "a@example.com",
      onProfile: () => {},
      onLogout: () => {},
      onUsage,
      phoneNav: null,
    };
    render(
      <MemoryRouter>
        <ChatShellContext.Provider value={shell}>
          <ChatTopbar
            title="Chat"
            onToggleSidebar={() => {}}
            sidebarOpen
            languages={[]}
            language="en"
            onLanguageChange={() => {}}
            isSuperuser={false}
          />
        </ChatShellContext.Provider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Logged in as a@example.com" }));
    fireEvent.click(within(screen.getByRole("menu")).getByText("My usage"));

    expect(onUsage).toHaveBeenCalledOnce();
  });

  it("opens the console's Usage page for someone with usage:read who has no admin button", () => {
    const onTeamUsage = vi.fn();
    const shell: ChatShell = {
      userEmail: "a@example.com",
      onProfile: () => {},
      onLogout: () => {},
      onUsage: () => {},
      onTeamUsage,
      phoneNav: null,
    };
    render(
      <MemoryRouter>
        <ChatShellContext.Provider value={shell}>
          <ChatTopbar
            title="Chat"
            onToggleSidebar={() => {}}
            sidebarOpen
            languages={[]}
            language="en"
            onLanguageChange={() => {}}
            isSuperuser={false}
          />
        </ChatShellContext.Provider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Logged in as a@example.com" }));
    fireEvent.click(within(screen.getByRole("menu")).getByText("Usage of all teams"));

    expect(onTeamUsage).toHaveBeenCalledOnce();
  });
});
