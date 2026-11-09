/** Approval limits: the longest grant and the evidence level per scope, and the list Bypass is for. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approvalLimitsApi,
  type ApprovalLimit, type ApprovalLimits, type BypassEntry,
  type BypassListChange,
  type BypassSides,
} from "~/api/approvalLimits";
import { renderPage } from "~/test/renderPage";

import { ApprovalLimitsTable } from "./ApprovalLimitsTable";

vi.mock("~/api/approvalLimits", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/approvalLimits")>();
  return {
    ...actual,
    approvalLimitsApi: { list: vi.fn(), set: vi.fn(), remove: vi.fn(), addToBypassList: vi.fn(), removeFromBypassList: vi.fn(),
      restrictSide: vi.fn(),
      liftSide: vi.fn(),
    },
  };
});
vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return {
    ...actual,
    agentsApi: {
      ...actual.agentsApi,
      list: vi.fn().mockResolvedValue([{ id: "a-1", name: "Office agent" }]),
      findPrincipals: vi.fn().mockResolvedValue([]),
    },
  };
});

const api = vi.mocked(approvalLimitsApi);

const TEAM: ApprovalLimit = {
  scope: "team",
  target_id: "g-1",
  label: "Finance",
  longest_minutes: 60,
  evidence_level: "calls",
  updated_at: "2026-11-07T09:00:00Z",
  updated_by: "ada@example.com",
};
const EVERYONE: ApprovalLimit = {
  ...TEAM,
  scope: "tenant",
  target_id: "default",
  label: null,
  longest_minutes: 120,
  evidence_level: null,
};
const ENTRY: BypassEntry = {
  scope: "agent",
  target_id: "a-1",
  label: "Office agent",
  created_at: "2026-11-07T09:30:00Z",
  created_by: "ada@example.com",
};

const state = (
  limits: ApprovalLimit[],
  bypass_for: BypassEntry[] = [],
  bypass_sides: BypassSides = { people: false, agents: bypass_for.length > 0 },
): ApprovalLimits => ({ limits, bypass_for, bypass_sides });
const change = (over: Partial<BypassListChange> = {}): BypassListChange => ({
  bypass_sides: { people: false, agents: false },
  ended: 0,
  removed: 0,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  api.list.mockResolvedValue(state([TEAM], [ENTRY]));
});

describe("ApprovalLimitsTable", () => {
  it("lists the limits and the Bypass entries", async () => {
    renderPage(<ApprovalLimitsTable />);

    expect(await screen.findByText("Finance")).toBeInTheDocument();
    expect(screen.getByText("Longest grant: 60 minutes")).toBeInTheDocument();
    expect(screen.getByText("Records: Calls")).toBeInTheDocument();
    expect(screen.getByText(/changed by ada@example.com/)).toBeInTheDocument();
    expect(screen.getByText("Office agent", { selector: ".MuiListItemText-primary" })).toBeInTheDocument();
    expect(screen.getByText(/added by ada@example.com/)).toBeInTheDocument();
  });

  it("says so when nothing is set", async () => {
    api.list.mockResolvedValue(state([]));
    renderPage(<ApprovalLimitsTable />);

    expect(await screen.findByText(/^No limits:/)).toBeInTheDocument();
    expect(screen.getByText(/^Any person may use Bypass/)).toBeInTheDocument();
  });

  it("sets a limit for everyone only once a value is given, and says how many grants it ended", async () => {
    api.set.mockResolvedValue({ ...EVERYONE, cut: 2 });
    renderPage(<ApprovalLimitsTable />);
    await screen.findByText("Finance");

    const set = screen.getByRole("button", { name: "Set limit" });
    expect(set).toBeDisabled();
    await userEvent.type(screen.getByRole("spinbutton", { name: "Longest grant (minutes)" }), "120");
    expect(set).toBeEnabled();
    api.list.mockResolvedValue(state([EVERYONE, TEAM]));
    await userEvent.click(set);

    await waitFor(() =>
      expect(api.set).toHaveBeenCalledWith("tenant", "default", { longest_minutes: 120, evidence_level: null }),
    );
    expect(await screen.findByText("Ended 2 running grants at the new limit")).toBeInTheDocument();
    expect(await screen.findByText("Longest grant: 120 minutes")).toBeInTheDocument();
  });

  it("sets only an evidence level, and refuses a value outside the range", async () => {
    api.set.mockResolvedValue({ ...EVERYONE, longest_minutes: null, evidence_level: "results", cut: 0 });
    renderPage(<ApprovalLimitsTable />);
    await screen.findByText("Finance");

    const minutes = screen.getByRole("spinbutton", { name: "Longest grant (minutes)" });
    await userEvent.type(minutes, "5");
    expect(screen.getByRole("button", { name: "Set limit" })).toBeDisabled();
    await userEvent.clear(minutes);

    await userEvent.click(screen.getByRole("combobox", { name: "Evidence level" }));
    await userEvent.click(await screen.findByRole("option", { name: /Results/ }));
    await userEvent.click(screen.getByRole("button", { name: "Set limit" }));

    await waitFor(() =>
      expect(api.set).toHaveBeenCalledWith("tenant", "default", { longest_minutes: null, evidence_level: "results" }),
    );
    expect(screen.queryByText(/Ended \d+ running grant/)).not.toBeInTheDocument();
  });

  it("sets a limit for an agent", async () => {
    api.set.mockResolvedValue({ ...EVERYONE, scope: "agent", target_id: "a-1", label: "Office agent", cut: 1 });
    renderPage(<ApprovalLimitsTable />);
    await screen.findByText("Finance");

    await userEvent.click(screen.getAllByRole("combobox", { name: "For" })[0]);
    await userEvent.click(await screen.findByRole("option", { name: "An agent" }));
    await userEvent.click(await screen.findByRole("combobox", { name: "Agent" }));
    await userEvent.click(await screen.findByRole("option", { name: "Office agent" }));
    await userEvent.type(screen.getByRole("spinbutton", { name: "Longest grant (minutes)" }), "30");
    await userEvent.click(screen.getByRole("button", { name: "Set limit" }));

    await waitFor(() =>
      expect(api.set).toHaveBeenCalledWith("agent", "a-1", { longest_minutes: 30, evidence_level: null }),
    );
    expect(await screen.findByText("Ended 1 running grant at the new limit")).toBeInTheDocument();
  });

  it("removes a limit", async () => {
    api.remove.mockResolvedValue(undefined);
    renderPage(<ApprovalLimitsTable />);

    const row = (await screen.findByText("Finance")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith("team", "g-1"));
  });

  it("adds to and removes from the Bypass list", async () => {
    api.addToBypassList.mockResolvedValue({ ...ENTRY, ended: 0 });
    api.removeFromBypassList.mockResolvedValue(change());
    api.list.mockResolvedValue(state([], []));
    renderPage(<ApprovalLimitsTable />);
    await screen.findByText(/^Any person may use Bypass/);

    const add = screen.getByRole("button", { name: "Add" });
    expect(add).toBeDisabled();
    // The "For" select of the Bypass list is the second one.
    await userEvent.click(screen.getAllByRole("combobox", { name: "For" })[1]);
    await userEvent.click(await screen.findByRole("option", { name: "An agent" }));
    const agentBoxes = screen.getAllByRole("combobox", { name: "Agent" });
    await userEvent.click(agentBoxes[agentBoxes.length - 1]);
    await userEvent.click(await screen.findByRole("option", { name: "Office agent" }));
    api.list.mockResolvedValue(state([], [ENTRY]));
    await userEvent.click(add);

    await waitFor(() => expect(api.addToBypassList).toHaveBeenCalledWith("agent", "a-1"));
    const row = (await screen.findByText("Office agent", { selector: ".MuiListItemText-primary" })).closest("li") as HTMLElement;
    api.list.mockResolvedValue(state([], []));
    await userEvent.click(within(row).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.removeFromBypassList).toHaveBeenCalledWith("agent", "a-1"));
  });

  it("names nobody: a restricted side with no entries shows a warning, each side on its own", async () => {
    api.list.mockResolvedValue(state([], [], { people: true, agents: false }));
    const { unmount } = renderPage(<ApprovalLimitsTable />);
    expect(await screen.findByText("Names no person: Bypass is for nobody.")).toBeInTheDocument();
    expect(screen.queryByText(/^Names no agent/)).not.toBeInTheDocument();
    expect(screen.getByText(/^Bypass is for the chats of any agent/)).toBeInTheDocument();
    unmount();

    api.list.mockResolvedValue(state([], [], { people: false, agents: true }));
    renderPage(<ApprovalLimitsTable />);
    expect(await screen.findByText("Names no agent: Bypass is for no agent's chats.")).toBeInTheDocument();
    expect(screen.queryByText(/^Names no person/)).not.toBeInTheDocument();
  });

  it("shows an unrestricted side as open to any person, with no entries", async () => {
    api.list.mockResolvedValue(state([], [], { people: false, agents: false }));
    renderPage(<ApprovalLimitsTable />);

    expect(await screen.findByText(/^Any person may use Bypass/)).toBeInTheDocument();
    expect(screen.getByText(/^Bypass is for the chats of any agent/)).toBeInTheDocument();
    expect(screen.queryByText(/^Names no/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bypass for any person" })).not.toBeInTheDocument();
  });

  it("lifts a side only after the confirmation", async () => {
    api.liftSide.mockResolvedValue(change({ removed: 1 }));
    renderPage(<ApprovalLimitsTable />);
    await screen.findByText("Office agent", { selector: ".MuiListItemText-primary" });

    await userEvent.click(screen.getByRole("button", { name: "Bypass for any agent" }));
    expect(api.liftSide).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/entries|taken off it|are taken off/)).toBeInTheDocument();
    api.list.mockResolvedValue(state([], [], { people: false, agents: false }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Bypass for any agent" }));

    await waitFor(() => expect(api.liftSide).toHaveBeenCalledWith("agents"));
    expect(await screen.findByText(/^Bypass is for the chats of any agent/)).toBeInTheDocument();
  });

  it("restricts a side with nobody named yet and says how many sessions it ended", async () => {
    api.list.mockResolvedValue(state([], [], { people: false, agents: false }));
    api.restrictSide.mockResolvedValue(change({ bypass_sides: { people: true, agents: false }, ended: 3 }));
    renderPage(<ApprovalLimitsTable />);
    await screen.findByText(/^Any person may use Bypass/);

    api.list.mockResolvedValue(state([], [], { people: true, agents: false }));
    await userEvent.click(screen.getByRole("button", { name: "Bypass for nobody yet" }));

    await waitFor(() => expect(api.restrictSide).toHaveBeenCalledWith("people"));
    expect(await screen.findByText("Ended 3 running Bypass sessions the list no longer names")).toBeInTheDocument();
    expect(await screen.findByText("Names no person: Bypass is for nobody.")).toBeInTheDocument();
  });

  it("says how many Bypass sessions a removal ended", async () => {
    api.removeFromBypassList.mockResolvedValue(change({ bypass_sides: { people: false, agents: true }, ended: 2 }));
    renderPage(<ApprovalLimitsTable />);
    const row = (await screen.findByText("Office agent", { selector: ".MuiListItemText-primary" })).closest("li") as HTMLElement;

    api.list.mockResolvedValue(state([], [], { people: false, agents: true }));
    await userEvent.click(within(row).getByRole("button", { name: "Remove" }));

    expect(await screen.findByText("Ended 2 running Bypass sessions the list no longer names")).toBeInTheDocument();
    expect(await screen.findByText("Names no agent: Bypass is for no agent's chats.")).toBeInTheDocument();
  });
});
