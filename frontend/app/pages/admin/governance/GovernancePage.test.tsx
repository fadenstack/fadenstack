/** Governance: tool rules and tool classes share one editor; decisions are filtered on the server. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hasFeature } from "~/api/about";
import { governanceApi, type ToolRule } from "~/api/governance";
import { APPROVAL_VIEWS, approvalActionsApi } from "~/api/approvalActions";
import { APPROVAL_LIMITS, approvalLimitsApi } from "~/api/approvalLimits";
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

vi.mock("~/api/approvalLimits", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/approvalLimits")>();
  return { ...actual, approvalLimitsApi: { list: vi.fn() } };
});
vi.mock("~/api/approvalActions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/approvalActions")>();
  return { ...actual, approvalActionsApi: { list: vi.fn(), exportUrl: vi.fn().mockReturnValue("/export") } };
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
  no_effect: false,
};

/** A rule made before allow was taken out. */
const OLD_ALLOW: ToolRule = {
  ...RULE,
  id: "r-old",
  name: "GitHub is fine",
  tool_pattern: "mcp.github.*",
  action: "allow",
  message: "GitHub tools may run.",
  no_effect: true,
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

  it("says on the rules tab and in the rule editor that a deny wins whatever the priority", async () => {
    open("rules");
    expect(await screen.findByText(/When several rules match, Deny wins over Ask first, whatever the priority/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    expect(within(await screen.findByRole("dialog")).getByText(/Deny always wins over Ask first\./)).toBeInTheDocument();
  });

  it("makes rules that deny or ask first, and no allow rules", async () => {
    open("rules");
    await userEvent.click(await screen.findByRole("button", { name: "Add rule" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("combobox", { name: "Action" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Deny", "Ask first"]);
  });

  it("lists an older allow rule as having no effect, and saves it only as a deny or ask first", async () => {
    api.toolRules.list.mockResolvedValue([RULE, OLD_ALLOW]);
    api.toolRules.update.mockResolvedValue({ ...OLD_ALLOW, action: "approval_required", no_effect: false });
    open("rules");

    const row = (await screen.findByText("mcp.github.*")).closest("tr")!;
    expect(within(row).getByText("Allow")).toBeInTheDocument();
    expect(within(row).getByText("No effect")).toBeInTheDocument();
    const denyRow = screen.getByText("mcp.github.delete_*").closest("tr")!;
    expect(within(denyRow).queryByText("No effect")).not.toBeInTheDocument();

    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Allow is gone: it changed nothing/)).toBeInTheDocument();
    const save = within(dialog).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await userEvent.click(within(dialog).getByRole("combobox", { name: "Action" }));
    await userEvent.click(await screen.findByRole("option", { name: "Ask first" }));
    expect(within(dialog).queryByText(/Allow is gone/)).not.toBeInTheDocument();
    await userEvent.click(save);

    await waitFor(() =>
      expect(api.toolRules.update).toHaveBeenCalledWith(
        "r-old",
        expect.objectContaining({ tool_pattern: "mcp.github.*", action: "approval_required" }),
      ),
    );
  });

  it("keeps the first match for tool classes", async () => {
    open("classes");
    await userEvent.click(await screen.findByRole("button", { name: "Add class" }));
    expect(within(await screen.findByRole("dialog")).getByText(/the first rule that matches decides/)).toBeInTheDocument();
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

  it("has no approval limits or agent actions tab where the server offers neither", async () => {
    open("approvals");
    expect(await screen.findByText("mcp.github.delete_*")).toBeInTheDocument(); // the first tab instead
    expect(screen.queryByRole("tab", { name: "Approval limits" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Agent actions" })).not.toBeInTheDocument();
  });

  it("shows the approval limits tab only with its feature", async () => {
    vi.mocked(hasFeature).mockImplementation(async (f) => f === APPROVAL_LIMITS);
    vi.mocked(approvalLimitsApi.list).mockResolvedValue({ limits: [], bypass_for: [] });
    open("approvals");

    expect(await screen.findByText(/^No limits:/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approval limits" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Agent actions" })).not.toBeInTheDocument();
  });

  it("shows the agent actions tab only with its feature", async () => {
    vi.mocked(hasFeature).mockImplementation(async (f) => f === APPROVAL_VIEWS);
    vi.mocked(approvalActionsApi.list).mockResolvedValue({
      data: [],
      next: null,
      since: "2026-11-06T10:00:00Z",
      until: "2026-11-07T10:00:00Z",
      places_truncated: false,
    });
    open("actions");

    expect(await screen.findByText("No agent actions in this period.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Agent actions" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Approval limits" })).not.toBeInTheDocument();
  });
});
