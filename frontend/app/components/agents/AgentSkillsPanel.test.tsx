/**
 * An agent's Skills tab (sdk/51 F01b): everyone's skills read only, this agent's own as a searchable set of
 * the skills the caller administers; one they do not administer stays as it is.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "~/api/http";
import { agentSkills, listSkills, setAgentSkills, type AgentSkill, type SkillSummary } from "~/api/skills";
import { agentFixture } from "~/test/agentFixtures";
import { renderPage } from "~/test/renderPage";

import { AgentSkillsPanel } from "./AgentSkillsPanel";

vi.mock("~/api/skills", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/skills")>();
  return { ...actual, agentSkills: vi.fn(), listSkills: vi.fn(), setAgentSkills: vi.fn() };
});

const AGENT = agentFixture({ id: "a-1" });

function assigned(id: string, name: string, more: Partial<AgentSkill> = {}): AgentSkill {
  return { id, name, slug: name.toLowerCase().replace(/ /g, "-"), status: "published", access: "admin", via: "agent", ...more };
}

function summary(id: string, name: string, description: string): SkillSummary {
  return {
    id,
    tenant_id: "default",
    name,
    slug: name.toLowerCase().replace(/ /g, "-"),
    description,
    owner_id: null,
    visibility: "tenant",
    activation: "auto",
    status: "published",
    enabled: true,
    priority: 50,
    tags: [],
    latest_version: 1,
    published_version: 1,
    created_by: "u-1",
    created_at: "2026-11-13T10:00:00Z",
    updated_at: "2026-11-13T10:00:00Z",
    access: "admin",
  } as SkillSummary;
}

const PAGES = summary("s-1", "Plant pages", "Reads EPLAN page references.");
const TONE = summary("s-2", "Formal tone", "Writes in the house style.");

function open(basePath = "/admin/agents") {
  renderPage(<AgentSkillsPanel agent={AGENT} basePath={basePath} />);
}

const picker = () => screen.getByRole("combobox", { name: "For this agent" });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(agentSkills).mockResolvedValue([]);
  vi.mocked(listSkills).mockResolvedValue({ items: [PAGES, TONE], total: 2, offset: 0, limit: 500 });
  vi.mocked(setAgentSkills).mockResolvedValue([]);
});

describe("AgentSkillsPanel", () => {
  it("asks for the skills the caller may assign: published ones", async () => {
    open();
    await screen.findByRole("combobox", { name: "For this agent" });
    expect(agentSkills).toHaveBeenCalledWith("a-1");
    expect(listSkills).toHaveBeenCalledWith(expect.objectContaining({ status: "published" }));
  });

  it("lists everyone's skills read only, each linking to its page", async () => {
    vi.mocked(agentSkills).mockResolvedValue([
      assigned("s-9", "House rules", { via: "everyone" }),
      assigned("s-8", "Old notes", { via: "everyone", status: "archived" }),
    ]);
    open();
    const card = (await screen.findByText("For everyone in your organisation")).closest(".MuiPaper-root") as HTMLElement;
    const rules = within(card).getByRole("link", { name: "House rules" });
    expect(rules).toHaveAttribute("href", "/admin/skills/s-9");
    expect(within(card).getByRole("link", { name: "Old notes" })).toHaveAttribute(
      "title",
      "Not published: it reaches no conversation.",
    );
    expect(card.querySelector(".MuiChip-deleteIcon")).toBeNull();
  });

  it("says when no skill is assigned to everyone", async () => {
    open();
    expect(await screen.findByText("No skill is assigned to everyone.")).toBeInTheDocument();
  });

  it("links a skill beside the chat when the agent page is beside the chat", async () => {
    vi.mocked(agentSkills).mockResolvedValue([assigned("s-1", "Plant pages")]);
    open("/agents");
    expect(await screen.findByRole("link", { name: "Plant pages" })).toHaveAttribute("href", "/skills/s-1");
  });

  it("finds a skill by its description too", async () => {
    open();
    await userEvent.type(await screen.findByRole("combobox", { name: "For this agent" }), "eplan");
    expect(await screen.findByRole("option", { name: /Plant pages/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Formal tone/ })).not.toBeInTheDocument();
  });

  it("shows Save only after a change and sends the set", async () => {
    vi.mocked(agentSkills).mockResolvedValue([assigned("s-1", "Plant pages")]);
    open();
    await screen.findByRole("link", { name: "Plant pages" });
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    await userEvent.type(picker(), "tone");
    await userEvent.click(await screen.findByRole("option", { name: /Formal tone/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setAgentSkills).toHaveBeenCalledWith("a-1", ["s-1", "s-2"]));
  });

  it("keeps a skill the caller does not administer, read only, and leaves it out of the save", async () => {
    vi.mocked(agentSkills).mockResolvedValue([
      assigned("s-1", "Plant pages"),
      assigned("s-7", "Someone elses", { access: "read" }),
    ]);
    open();
    const theirs = await screen.findByRole("link", { name: "Someone elses" });
    expect(theirs).toHaveAttribute("title", "Assigned by someone who manages that skill");
    expect(theirs.querySelector(".MuiChip-deleteIcon")).toBeNull();
    const mine = screen.getByRole("link", { name: "Plant pages" });
    await userEvent.click(mine.querySelector(".MuiChip-deleteIcon") as Element);
    // Backspace takes the last chip away: not one the caller may not remove.
    await userEvent.type(picker(), "{Backspace}");
    expect(screen.getByRole("link", { name: "Someone elses" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setAgentSkills).toHaveBeenCalledWith("a-1", []));
  });

  it("says in its own words a skill the caller may not assign", async () => {
    vi.mocked(setAgentSkills).mockRejectedValue(new ApiError("You may not assign the skill s-2.", 403, "not_your_skill"));
    open();
    await userEvent.type(await screen.findByRole("combobox", { name: "For this agent" }), "tone");
    await userEvent.click(await screen.findByRole("option", { name: /Formal tone/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("You can only assign skills you administer.")).toBeInTheDocument();
  });

  it("says so when there is nothing the caller may assign", async () => {
    vi.mocked(listSkills).mockResolvedValue({ items: [], total: 0, offset: 0, limit: 500 });
    open();
    await userEvent.click(await screen.findByRole("combobox", { name: "For this agent" }));
    expect(await screen.findByText("There is no published skill that you administer.")).toBeInTheDocument();
  });
});
