/** Agent actions: listed with their state, filtered on the server, paged, exported, and for the auditor role only. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { approvalActionsApi, type ActionPage, type AgentAction } from "~/api/approvalActions";
import { ApiError } from "~/api/http";
import { renderPage } from "~/test/renderPage";

import { AgentActionsTable } from "./AgentActionsTable";

vi.mock("~/api/approvalActions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/approvalActions")>();
  return {
    ...actual,
    approvalActionsApi: { list: vi.fn(), exportUrl: vi.fn((f: object) => `/export?f=${JSON.stringify(f)}`) },
  };
});
vi.mock("~/api/governance", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/governance")>();
  return { ...actual, governanceApi: { grantCalls: vi.fn().mockResolvedValue({ data: [], device_only: [] }) } };
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

const api = vi.mocked(approvalActionsApi);

const ACTION: AgentAction = {
  key: "decision:1",
  at: "2026-11-07T10:00:00Z",
  source: "gateway",
  state: "once",
  tool: "mail.send_draft",
  side: "gateway",
  user_id: "u-1",
  person: "anna@example.com",
  agent_id: "a-1",
  agent: "Office agent",
  agent_version: 3,
  prompt: null,
  device_id: "dev-1",
  conversation_id: null,
  turn_id: "turn-1",
  request_id: null,
  model: "qwen3.8-27b",
  grant_id: null,
  grant_kind: null,
  policy: null,
  outcome: "ok",
};
const BYPASS: AgentAction = {
  ...ACTION,
  key: "grant-call:2",
  source: "device",
  state: "bypass",
  tool: "files.read",
  place: "C:\\Users\\anna\\report.docx",
  grant_id: "g-9",
  grant_kind: "bypass",
};

const page = (data: AgentAction[], next: ActionPage["next"] = null): ActionPage => ({
  data,
  next,
  since: "2026-11-06T10:00:00Z",
  until: "2026-11-07T10:00:00Z",
  places_truncated: false,
});

beforeEach(() => {
  vi.clearAllMocks();
  api.list.mockResolvedValue(page([ACTION, BYPASS]));
});

describe("AgentActionsTable", () => {
  it("lists the actions with their state, agent version and place", async () => {
    renderPage(<AgentActionsTable />);

    expect(await screen.findByText("mail.send_draft")).toBeInTheDocument();
    expect(screen.getAllByText("anna@example.com")).toHaveLength(2);
    expect(screen.getAllByText("Office agent v3")).toHaveLength(2);
    expect(screen.getByText("Once")).toBeInTheDocument();
    expect(screen.getByText("Bypass")).toBeInTheDocument();
    expect(screen.getByText("Gateway's record")).toBeInTheDocument();
    expect(screen.getByText("Device's report")).toBeInTheDocument();
    expect(screen.getByText("C:\\Users\\anna\\report.docx")).toBeInTheDocument();
    expect(screen.getByText(/works agreement/)).toBeInTheDocument();
    // Only the row with a grant has the Calls button.
    expect(screen.getAllByRole("button", { name: "Calls" })).toHaveLength(1);
  });

  it("opens the calls of the grant behind an action", async () => {
    renderPage(<AgentActionsTable />);

    await userEvent.click(await screen.findByRole("button", { name: "Calls" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("No calls were recorded.")).toBeInTheDocument();
  });

  it("sends the filter when Search is pressed, and exports the same filter", async () => {
    renderPage(<AgentActionsTable />);
    await screen.findByText("mail.send_draft");
    expect(api.list).toHaveBeenCalledWith({}) ;

    await userEvent.type(screen.getByRole("textbox", { name: "Device" }), "dev-1");
    await userEvent.type(screen.getByRole("textbox", { name: /Workspace/ }), "report.docx");
    api.list.mockResolvedValue(page([ACTION]));
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(api.list).toHaveBeenLastCalledWith({
        person: null,
        agent: null,
        device: "dev-1",
        workspace: "report.docx",
        since: null,
        until: null,
      }),
    );
    await waitFor(() => expect(screen.queryByText("files.read")).not.toBeInTheDocument());
    const link = screen.getByRole("link", { name: /Export \(CloudEvents\)/ });
    expect(link).toHaveAttribute("download");
    expect(link.getAttribute("href")).toContain('"device":"dev-1"');
    expect(link.getAttribute("href")).toContain('"workspace":"report.docx"');
  });

  it("sends the times with the browser's offset", async () => {
    renderPage(<AgentActionsTable />);
    await screen.findByText("mail.send_draft");

    const from = screen.getByLabelText("From");
    await userEvent.type(from, "2026-11-07T08:30");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      const filter = api.list.mock.calls[api.list.mock.calls.length - 1][0] as { since: string | null };
      expect(filter.since).toMatch(/^2026-11-07T08:30:00[+-]\d\d:\d\d$/);
    });
  });

  it("appends the next page", async () => {
    const more: AgentAction = { ...ACTION, key: "decision:0", tool: "calendar.add", state: "declined" };
    api.list.mockResolvedValueOnce(page([ACTION], { before_at: ACTION.at, before_key: ACTION.key }));
    api.list.mockResolvedValueOnce(page([more]));
    renderPage(<AgentActionsTable />);

    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByText("calendar.add")).toBeInTheDocument();
    expect(screen.getByText("mail.send_draft")).toBeInTheDocument();
    expect(screen.getByText("Declined")).toBeInTheDocument();
    expect(api.list).toHaveBeenLastCalledWith({}, { before_at: ACTION.at, before_key: ACTION.key });
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("says the auditor role is needed when reading is refused", async () => {
    api.list.mockRejectedValue(new ApiError("forbidden", 403));
    renderPage(<AgentActionsTable />);

    expect(await screen.findByText(/needs the role approvals_auditor/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
