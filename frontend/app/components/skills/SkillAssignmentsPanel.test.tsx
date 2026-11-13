/**
 * A skill's Assignments tab (sdk/51 F01b): agents are picked from a searchable list and saved as a set; nobody
 * types an id. Workspace and project assignments are not offered; old ones show under "Not in effect".
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentsApi } from "~/api/agents";
import { ApiError } from "~/api/http";
import {
  createAssignment,
  deleteAssignment,
  listAssignments,
  setSkillAgents,
  type SkillAssignment,
} from "~/api/skills";
import { agentFixture } from "~/test/agentFixtures";
import { renderPage } from "~/test/renderPage";

import SkillAssignmentsPanel from "./SkillAssignmentsPanel";

vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return { ...actual, agentsApi: { ...actual.agentsApi, list: vi.fn() } };
});
vi.mock("~/api/skills", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/skills")>();
  return {
    ...actual,
    listAssignments: vi.fn(),
    createAssignment: vi.fn(),
    deleteAssignment: vi.fn(),
    setSkillAgents: vi.fn(),
  };
});

const agents = vi.mocked(agentsApi);
const SKILL = "s-1";

function row(target_type: SkillAssignment["target_type"], target_id: string | null, more: Partial<SkillAssignment> = {}) {
  return {
    id: `r-${target_type}-${target_id ?? "all"}`,
    skill_id: SKILL,
    target_type,
    target_id,
    enabled: true,
    priority_override: null,
    created_at: "2026-11-13T10:00:00Z",
    ...more,
  } satisfies SkillAssignment;
}

const EXCEL = agentFixture({ id: "a-1", slug: "excel-assistant", name: "Excel assistant" });
const WORD = agentFixture({ id: "a-2", slug: "word-helper", name: "Word helper" });
const OLD = agentFixture({ id: "a-3", slug: "old-bot", name: "Old bot", status: "retired" });

/** Opens the tab; `permissions` are the signed-in user's (none given: everything allowed). */
function open(permissions?: string[]) {
  renderPage(<SkillAssignmentsPanel skillId={SKILL} />, {
    context: permissions ? { permissions: new Set(permissions) } : undefined,
  });
}

const picker = () => screen.getByRole("combobox", { name: "Agents" });

beforeEach(() => {
  vi.clearAllMocks();
  agents.list.mockResolvedValue([EXCEL, WORD, OLD]);
  vi.mocked(listAssignments).mockResolvedValue([]);
  vi.mocked(setSkillAgents).mockResolvedValue([]);
  vi.mocked(createAssignment).mockResolvedValue(row("tenant", null));
  vi.mocked(deleteAssignment).mockResolvedValue(undefined);
});

describe("SkillAssignmentsPanel", () => {
  it("finds agents by name and by address, and offers no retired one", async () => {
    open();
    await userEvent.type(await screen.findByRole("combobox", { name: "Agents" }), "word");
    expect(await screen.findByRole("option", { name: /Word helper/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Excel assistant/ })).not.toBeInTheDocument();

    await userEvent.clear(picker());
    await userEvent.type(picker(), "excel-ass");
    expect(await screen.findByRole("option", { name: /Excel assistant/ })).toBeInTheDocument();

    await userEvent.clear(picker());
    await userEvent.type(picker(), "old");
    expect(await screen.findByText("No agent matches.")).toBeInTheDocument();
  });

  it("shows Save only after a change and saves the whole set", async () => {
    vi.mocked(listAssignments).mockResolvedValue([row("tenant", null), row("assistant", "a-1")]);
    open();
    expect(await screen.findByRole("link", { name: /Excel assistant/ })).toHaveAttribute(
      "href",
      "/admin/agents/excel-assistant",
    );
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();

    await userEvent.type(picker(), "word");
    await userEvent.click(await screen.findByRole("option", { name: /Word helper/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setSkillAgents).toHaveBeenCalledWith(SKILL, ["a-1", "a-2"]));
    expect(createAssignment).not.toHaveBeenCalled();
  });

  it("removes an agent's chip without following its link", async () => {
    vi.mocked(listAssignments).mockResolvedValue([row("assistant", "a-1"), row("assistant", "a-2")]);
    open();
    const chip = await screen.findByRole("link", { name: /Excel assistant/ });
    const followed = fireEvent.click(chip.querySelector(".MuiChip-deleteIcon") as Element);
    expect(followed).toBe(false);
    expect(screen.queryByRole("link", { name: /Excel assistant/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setSkillAgents).toHaveBeenCalledWith(SKILL, ["a-2"]));
  });

  it("puts the set back on Cancel", async () => {
    vi.mocked(listAssignments).mockResolvedValue([row("assistant", "a-1")]);
    open();
    await userEvent.type(await screen.findByRole("combobox", { name: "Agents" }), "word");
    await userEvent.click(await screen.findByRole("option", { name: /Word helper/ }));
    expect(screen.getByRole("link", { name: /Word helper/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("link", { name: /Word helper/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(setSkillAgents).not.toHaveBeenCalled();
  });

  it("keeps a retired agent greyed, and shows a gone one as no longer existing", async () => {
    vi.mocked(listAssignments).mockResolvedValue([
      row("assistant", "a-3"),
      row("assistant", "a-gone", { priority_override: 70 }),
    ]);
    open();
    expect(await screen.findByRole("link", { name: /Old bot/ })).toHaveAttribute("title", "Retired");
    const gone = screen.getByText("Agent no longer exists");
    expect(gone.closest("[title]")).toHaveAttribute("title", "a-gone");
  });

  it("names by id an assigned agent the caller cannot see, rather than calling it gone", async () => {
    vi.mocked(listAssignments).mockResolvedValue([row("assistant", "a-hidden")]);
    open(["skills:read", "skills:manage"]);
    const chip = await screen.findByText("a-hidden");
    expect(chip.closest("[title]")).toHaveAttribute("title", "An agent you cannot see");
    expect(screen.queryByText("Agent no longer exists")).not.toBeInTheDocument();
  });

  it("says so when the agents cannot be listed, and shows the assigned ones by id", async () => {
    agents.list.mockRejectedValue(new Error("403"));
    vi.mocked(listAssignments).mockResolvedValue([row("assistant", "a-1")]);
    open();
    expect(await screen.findByText(/could not be read/)).toBeInTheDocument();
    expect(screen.getByText("a-1")).toBeInTheDocument();
    expect(picker()).toBeDisabled();
  });

  it("lists workspace and project assignments as not in effect, each with Remove", async () => {
    vi.mocked(listAssignments).mockResolvedValue([row("workspace", "w-1"), row("project", "p-1"), row("tenant", null)]);
    open();
    const card = (await screen.findByText("Not in effect")).closest(".MuiPaper-root") as HTMLElement;
    expect(within(card).getByText("Workspace: w-1")).toBeInTheDocument();
    expect(within(card).getByText("Project: p-1")).toBeInTheDocument();
    await userEvent.click(within(card).getAllByRole("button", { name: "Remove" })[0]);
    await waitFor(() => expect(deleteAssignment).toHaveBeenCalledWith(SKILL, "r-workspace-w-1"));
    expect(screen.queryByRole("option", { name: /Workspace/ })).not.toBeInTheDocument();
  });

  it("has no not-in-effect section when there is nothing old", async () => {
    vi.mocked(listAssignments).mockResolvedValue([row("assistant", "a-1")]);
    open();
    await screen.findByRole("link", { name: /Excel assistant/ });
    expect(screen.queryByText("Not in effect")).not.toBeInTheDocument();
  });

  it("switches the skill on for everyone in the organisation", async () => {
    open();
    const everyone = await screen.findByRole("switch", { name: "Everyone in your organisation" });
    expect(everyone).not.toBeChecked();
    await userEvent.click(everyone);
    await waitFor(() => expect(createAssignment).toHaveBeenCalledWith(SKILL, { target_type: "tenant" }));
    expect(deleteAssignment).not.toHaveBeenCalled();
  });

  it("removes the everyone assignment when switched off", async () => {
    vi.mocked(listAssignments).mockResolvedValue([row("tenant", null)]);
    open();
    const everyone = await screen.findByRole("switch", { name: "Everyone in your organisation" });
    await waitFor(() => expect(everyone).toBeChecked());
    await userEvent.click(everyone);
    await waitFor(() => expect(deleteAssignment).toHaveBeenCalledWith(SKILL, "r-tenant-all"));
  });

  it("offers every organisation only to those who administer every skill", async () => {
    open(["skills:read", "skills:manage"]);
    expect(await screen.findByRole("switch", { name: "Every organisation" })).toBeEnabled();
  });

  it("does not offer every organisation without skills:manage", async () => {
    open(["skills:read"]);
    await screen.findByRole("switch", { name: "Everyone in your organisation" });
    expect(screen.queryByRole("switch", { name: "Every organisation" })).not.toBeInTheDocument();
  });

  it("says in its own words an agent the server does not know", async () => {
    vi.mocked(setSkillAgents).mockRejectedValue(
      new ApiError("No such agent: a-2", 422, "unknown_agent", { code: "unknown_agent", ids: ["a-2"] }),
    );
    open();
    await userEvent.type(await screen.findByRole("combobox", { name: "Agents" }), "word");
    await userEvent.click(await screen.findByRole("option", { name: /Word helper/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("No such agent: a-2")).toBeInTheDocument();
  });
});
