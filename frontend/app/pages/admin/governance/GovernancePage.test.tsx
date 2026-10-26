/** Governance: tool rules and tool classes share one editor; decisions are filtered on the server. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hasFeature } from "~/api/about";
import { governanceApi, type ToolRule } from "~/api/governance";
import { TOOL_MODE_CEILINGS, toolCeilingsApi } from "~/api/toolCeilings";
import { renderPage } from "~/test/renderPage";

import GovernancePage from "./GovernancePage";

vi.mock("~/api/governance", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/governance")>();
  const rules = () => ({ list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() });
  return { ...actual, governanceApi: { toolRules: rules(), toolClasses: rules(), decisions: vi.fn() } };
});
vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return {
    ...actual,
    agentsApi: { ...actual.agentsApi, list: vi.fn().mockResolvedValue([]), namePrincipals: vi.fn().mockResolvedValue([]) },
  };
});

vi.mock("~/api/about", () => ({ hasFeature: vi.fn() }));
vi.mock("~/api/toolCeilings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/toolCeilings")>();
  return { ...actual, toolCeilingsApi: { list: vi.fn(), set: vi.fn(), remove: vi.fn() } };
});

const api = vi.mocked(governanceApi, { deep: true });
const ceilings = vi.mocked(toolCeilingsApi);

const RULE: ToolRule = {
  id: "r-1",
  tenant_id: "default",
  name: "No GitHub deletes",
  tool_pattern: "mcp.github.delete_*",
  action: "deny",
  message: "Deleting on GitHub is not allowed here.",
  category: "tool_rule",
  priority: 10,
  enabled: true,
  created_at: null,
  updated_at: null,
};

const open = (tab: string) =>
  renderPage(<GovernancePage />, { path: "/admin/governance", initialPath: `/admin/governance?tab=${tab}` });

beforeEach(() => {
  vi.clearAllMocks();
  api.toolRules.list.mockResolvedValue([RULE]);
  api.toolClasses.list.mockResolvedValue([]);
  api.decisions.mockResolvedValue([]);
  vi.mocked(hasFeature).mockResolvedValue(false);
});

describe("GovernancePage", () => {
  it("lists tool rules and adds one", async () => {
    api.toolRules.create.mockResolvedValue({ ...RULE, id: "r-2" });
    open("rules");

    expect(await screen.findByText("mcp.github.delete_*")).toBeInTheDocument();
    expect(screen.getByText("Deny")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const dialog = await screen.findByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Ask before mail");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Tool pattern" }), "send_mail");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Message to the user" }), "Sending mail needs your OK.");
    await userEvent.click(save);

    await waitFor(() =>
      expect(api.toolRules.create).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Ask before mail", tool_pattern: "send_mail", action: "deny", enabled: true }),
      ),
    );
    expect(api.toolRules.list).toHaveBeenCalledTimes(2);
  });

  it("removes a rule after a confirmation", async () => {
    api.toolRules.remove.mockResolvedValue(undefined);
    open("rules");

    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.toolRules.remove).toHaveBeenCalledWith("r-1"));
  });

  it("opens the tab in the address", async () => {
    open("classes");
    expect(await screen.findByText(/No tool classes/)).toBeInTheDocument();
    expect(api.toolRules.list).not.toHaveBeenCalled();
  });

  it("has no tool mode limits where the server offers none", async () => {
    open("limits");
    expect(await screen.findByText("mcp.github.delete_*")).toBeInTheDocument(); // the first tab instead
    expect(screen.queryByRole("tab", { name: "Tool mode limits" })).not.toBeInTheDocument();
  });

  it("sets, changes and removes tool mode limits where the server offers them", async () => {
    vi.mocked(hasFeature).mockImplementation(async (f) => f === TOOL_MODE_CEILINGS);
    const team = {
      scope: "team" as const,
      target_id: "g-1",
      label: "Finance",
      mode: "read_only" as const,
      updated_at: "2026-10-26T09:00:00Z",
      updated_by: "ada@example.com",
    };
    const everyone = { ...team, scope: "tenant" as const, target_id: "default", label: null, mode: "ask" as const };
    ceilings.list.mockResolvedValueOnce([team]).mockResolvedValue([everyone, team]);
    ceilings.set.mockResolvedValue(everyone);
    ceilings.remove.mockResolvedValue(undefined);
    open("limits");

    expect(await screen.findByText("Finance")).toBeInTheDocument();
    expect(screen.getByText(/changed by ada@example.com/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Set limit" }));
    await waitFor(() => expect(ceilings.set).toHaveBeenCalledWith("tenant", "default", "ask"));
    expect(
      await screen.findByText("Every chat, in the console and through agents.", { selector: ".MuiListItemText-primary" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("combobox", { name: "Limit for Finance" }));
    await userEvent.click(await screen.findByRole("option", { name: "Off" }));
    await waitFor(() => expect(ceilings.set).toHaveBeenCalledWith("team", "g-1", "off"));

    const row = screen.getByText("Finance").closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(ceilings.remove).toHaveBeenCalledWith("team", "g-1"));
  });
});
