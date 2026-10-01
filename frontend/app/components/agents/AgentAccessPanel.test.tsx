/** Who may use an agent and who maintains it: edited in place, saved as a whole. */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentsApi } from "~/api/agents";
import { agentFixture } from "~/test/agentFixtures";

import { AgentAccessPanel } from "./AgentAccessPanel";

vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return {
    ...actual,
    agentsApi: {
      ...actual.agentsApi,
      grants: vi.fn(),
      setGrants: vi.fn(),
      members: vi.fn(),
      setMembers: vi.fn(),
      findPrincipals: vi.fn(),
      explainAccess: vi.fn(),
    },
  };
});

const api = vi.mocked(agentsApi);

beforeEach(() => {
  vi.clearAllMocks();
  api.grants.mockResolvedValue([{ principal_type: "group", principal_id: "g-1", label: "Finance" }]);
  api.members.mockResolvedValue([{ user_id: "u-1", role: "maintainer", label: "ana@example.com" }]);
});

describe("AgentAccessPanel", () => {
  it("adds a team and saves the grants", async () => {
    api.findPrincipals.mockResolvedValue([
      { type: "group", id: "g-1", label: "Finance" },
      { type: "group", id: "g-2", label: "Controlling" },
    ]);
    api.setGrants.mockResolvedValue([]);
    render(<AgentAccessPanel agent={agentFixture()} />);

    expect(await screen.findByText("Finance")).toBeInTheDocument();
    expect(screen.getByText("ana@example.com")).toBeInTheDocument();
    const [saveGrants] = screen.getAllByRole("button", { name: "Save" });
    expect(saveGrants).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText("Add a person or team…"), "co");
    // Already granted: not offered again.
    await userEvent.click(await screen.findByRole("option", { name: "Controlling" }));
    expect(screen.queryByRole("option", { name: "Finance" })).not.toBeInTheDocument();

    await userEvent.click(saveGrants);
    await waitFor(() =>
      expect(api.setGrants).toHaveBeenCalledWith("excel-assistant", [
        { principal_type: "group", principal_id: "g-1" },
        { principal_type: "group", principal_id: "g-2" },
      ]),
    );
  });

  it("says why someone may not use the agent", async () => {
    api.findPrincipals.mockResolvedValue([{ type: "user", id: "u-9", label: "bob@example.com" }]);
    api.explainAccess.mockResolvedValue({ allowed: false, code: "no_grant", detail: "Not granted to bob or any of their teams." });
    render(<AgentAccessPanel agent={agentFixture()} />);

    await userEvent.type(await screen.findByPlaceholderText("Find a user…"), "bob");
    await userEvent.click(await screen.findByRole("option", { name: "bob@example.com" }));

    expect(await screen.findByText("bob@example.com may not use this agent.")).toBeInTheDocument();
    expect(screen.getByText("Not granted to bob or any of their teams.")).toBeInTheDocument();
    expect(api.explainAccess).toHaveBeenCalledWith("excel-assistant", "u-9");
  });

  it("only shows the lists to someone who does not manage the agent", async () => {
    render(<AgentAccessPanel agent={agentFixture({ can_manage: false })} />);
    expect(await screen.findByText("Finance")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Add a person or team…")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });
});
