/** Who may use an agent and who maintains it: edited in place, saved as a whole. */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hasFeature } from "~/api/about";
import { agentExclusionsApi } from "~/api/agentExclusions";
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
      liftRestriction: vi.fn(),
    },
  };
});

vi.mock("~/api/about", () => ({ hasFeature: vi.fn() }));
vi.mock("~/api/agentExclusions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agentExclusions")>();
  return { ...actual, agentExclusionsApi: { list: vi.fn(), add: vi.fn(), remove: vi.fn() } };
});

const api = vi.mocked(agentsApi);
const exclusions = vi.mocked(agentExclusionsApi);

beforeEach(() => {
  vi.clearAllMocks();
  api.grants.mockResolvedValue([{ principal_type: "group", principal_id: "g-1", label: "Finance" }]);
  api.members.mockResolvedValue([{ user_id: "u-1", role: "maintainer", label: "ana@example.com" }]);
  vi.mocked(hasFeature).mockResolvedValue(false);
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

  it("excludes a person with a reason, and lets them back", async () => {
    vi.mocked(hasFeature).mockResolvedValue(true);
    const excluded = {
      user_id: "u-7",
      email: "carl@example.com",
      reason: "Moved to sales",
      created_at: "2026-10-25T10:00:00Z",
      created_by: "ana@example.com",
    };
    exclusions.list.mockResolvedValueOnce([]).mockResolvedValueOnce([excluded]).mockResolvedValueOnce([]);
    exclusions.add.mockResolvedValue(excluded);
    exclusions.remove.mockResolvedValue(undefined);
    api.findPrincipals.mockResolvedValue([
      { type: "user", id: "u-1", label: "ana@example.com" },
      { type: "user", id: "u-7", label: "carl@example.com" },
    ]);
    render(<AgentAccessPanel agent={agentFixture()} />);

    expect(await screen.findByText("Nobody is excluded.")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Exclude a person…"), "ex");
    await userEvent.click(await screen.findByRole("option", { name: "carl@example.com" }));
    // A maintainer always keeps the agent: not offered.
    expect(screen.queryByRole("option", { name: "ana@example.com" })).not.toBeInTheDocument();

    const exclude = screen.getByRole("button", { name: "Exclude" });
    await userEvent.type(screen.getByLabelText("Reason"), "No");
    expect(exclude).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason"), "pe, moved to sales");
    await userEvent.click(exclude);
    await waitFor(() => expect(exclusions.add).toHaveBeenCalledWith("excel-assistant", "u-7", "Nope, moved to sales"));
    expect(await screen.findByText("carl@example.com")).toBeInTheDocument();
    expect(screen.getByText(/Moved to sales · excluded by ana@example.com/)).toBeInTheDocument();

    const row = screen.getByText("carl@example.com").closest("li");
    await userEvent.click(within(row as HTMLElement).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(exclusions.remove).toHaveBeenCalledWith("excel-assistant", "u-7"));
    expect(await screen.findByText("Nobody is excluded.")).toBeInTheDocument();
  });

  it("offers no exclusions where the server has none, nor to someone who does not manage the agent", async () => {
    render(<AgentAccessPanel agent={agentFixture()} />);
    expect(await screen.findByText("Finance")).toBeInTheDocument();
    expect(screen.queryByText("Excluded people")).not.toBeInTheDocument();

    vi.mocked(hasFeature).mockResolvedValue(true);
    render(<AgentAccessPanel agent={agentFixture({ can_manage: false })} />);
    await waitFor(() => expect(hasFeature).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Excluded people")).not.toBeInTheDocument();
    expect(exclusions.list).not.toHaveBeenCalled();
  });

  it("says when only the maintainers can use the agent, and opens it again on request", async () => {
    const onChanged = vi.fn();
    api.liftRestriction.mockResolvedValue(agentFixture());
    const closed = agentFixture({ unenforced: ["exclusions"], unenforced_restrictions: ["exclusions"] });
    render(<AgentAccessPanel agent={closed} onChanged={onChanged} />);
    expect(
      await screen.findByText(
        "Only its maintainers can use this agent: it uses settings that need Fadenstack Enterprise (exclusions).",
      ),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open to everyone granted" }));
    expect(await screen.findByText(/Lifts exclusions: everyone the agent is granted to/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open it" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api.liftRestriction).toHaveBeenCalledWith("excel-assistant", "exclusions");
  });

  it("offers no opening to someone who does not manage the agent", async () => {
    render(<AgentAccessPanel agent={agentFixture({ can_manage: false, unenforced: ["exclusions"] })} />);
    expect(await screen.findByText(/Only its maintainers can use this agent/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open to everyone granted" })).not.toBeInTheDocument();
  });
});
