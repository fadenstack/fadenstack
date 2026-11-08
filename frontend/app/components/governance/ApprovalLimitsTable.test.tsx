/** Approval limits: the longest grant and the evidence level per scope, and the list Bypass is for. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { approvalLimitsApi, type ApprovalLimit, type ApprovalLimits, type BypassEntry } from "~/api/approvalLimits";
import { renderPage } from "~/test/renderPage";

import { ApprovalLimitsTable } from "./ApprovalLimitsTable";

vi.mock("~/api/approvalLimits", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/approvalLimits")>();
  return {
    ...actual,
    approvalLimitsApi: { list: vi.fn(), set: vi.fn(), remove: vi.fn(), addToBypassList: vi.fn(), removeFromBypassList: vi.fn() },
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

const state = (limits: ApprovalLimit[], bypass_for: BypassEntry[] = []): ApprovalLimits => ({ limits, bypass_for });

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
    expect(screen.getByText(/^Nobody is named/)).toBeInTheDocument();
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
    api.addToBypassList.mockResolvedValue({ ...ENTRY, scope: "user", target_id: "u-1", label: "anna@example.com" });
    api.removeFromBypassList.mockResolvedValue(undefined);
    api.list.mockResolvedValue(state([], []));
    renderPage(<ApprovalLimitsTable />);
    await screen.findByText(/^Nobody is named/);

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
});
