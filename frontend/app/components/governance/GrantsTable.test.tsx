/** Running grants: listed with what they cover, and an administrator can revoke one. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { governanceApi, type Grant } from "~/api/governance";
import { ApiError } from "~/api/http";
import { renderPage } from "~/test/renderPage";

import { GrantsTable } from "./GrantsTable";

vi.mock("~/api/governance", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/governance")>();
  return { ...actual, governanceApi: { grants: vi.fn(), revokeGrant: vi.fn(), grantCalls: vi.fn() } };
});
vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return {
    ...actual,
    agentsApi: {
      ...actual.agentsApi,
      list: vi.fn().mockResolvedValue([{ id: "a-1", name: "Office agent" }]),
      namePrincipals: vi.fn().mockResolvedValue([]),
    },
  };
});

const api = vi.mocked(governanceApi, { deep: true });

const TIME: Grant = {
  id: "g-1",
  kind: "time",
  status: "active",
  user_id: "u-1",
  agent_id: "a-1",
  agent_version: "0.2.12",
  device_id: "dev-1",
  conversation_id: null,
  scope: { tools: ["mail.send_draft"] },
  issued_at: "2026-11-07T10:00:00Z",
  expires_at: "2026-11-07T11:00:00Z",
  paused_at: null,
  max_calls: 20,
  calls_used: 3,
  remaining_s: 1790,
  flags: 0,
};

const BYPASS: Grant = {
  ...TIME,
  id: "g-3",
  kind: "bypass",
  scope: {},
  max_calls: null,
  calls_used: 5,
  flags: 2,
};

const WORK: Grant = {
  ...TIME,
  id: "g-2",
  kind: "work",
  status: "paused",
  scope: {
    classes: ["read", "write"],
    targets: [{ type: "file", identifiers: ["C:\\Users\\anna\\Documents\\report.docx"] }],
  },
  max_calls: null,
  calls_used: 7,
  remaining_s: 600,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.grants.mockResolvedValue([TIME, WORK]);
  api.revokeGrant.mockResolvedValue(undefined);
});

/** Opens the calls dialog; the rows are drawn again as the agent names arrive, so a click may need repeating. */
const openCalls = async () => {
  await waitFor(async () => {
    await userEvent.click(screen.getByRole("button", { name: "Show calls" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
  return screen.getByRole("dialog");
};

/** The rows and the agent names are in. */
const settled = async () => {
  await screen.findByText("mail.send_draft");
  await waitFor(() => expect(screen.getAllByText("Office agent")).toHaveLength(2));
};

describe("GrantsTable", () => {
  it("lists a tool grant and a working-alongside grant", async () => {
    renderPage(<GrantsTable />);

    await settled();
    expect(screen.getByText("mail.send_draft")).toBeInTheDocument();
    expect(screen.getByText("For a time")).toBeInTheDocument();
    expect(screen.getByText("Working alongside")).toBeInTheDocument();
    expect(screen.getByText("report.docx")).toBeInTheDocument();
    expect(screen.getByText("Read, Write")).toBeInTheDocument();
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("3 / 20")).toBeInTheDocument();
    expect(screen.getByText("30 minutes left")).toBeInTheDocument();
  });

  it("labels a Bypass grant and shows its differences", async () => {
    api.grants.mockResolvedValue([TIME, BYPASS]);
    renderPage(<GrantsTable />);
    await screen.findByText("mail.send_draft");
    await waitFor(() => expect(screen.getAllByText("Office agent")).toHaveLength(2));

    expect(await screen.findByText("Bypass")).toBeInTheDocument();
    expect(screen.getByText("2 differences")).toBeInTheDocument();
    // Only a Bypass grant has calls to look at.
    expect(screen.getAllByRole("button", { name: "Show calls" })).toHaveLength(1);
  });

  it("lists the calls of a Bypass grant", async () => {
    api.grants.mockResolvedValue([BYPASS]);
    api.grantCalls.mockResolvedValue({
      data: [
        { id: "c-1", request_id: "r-1", call_id: "call-aaa", tool_id: "mail.send", side: "gateway", created_at: "2026-11-07T10:05:00Z", reported_at: null, flag: "gateway_only" },
        { id: "c-2", request_id: "r-1", call_id: "call-bbb", tool_id: "files.read", side: "client", created_at: "2026-11-07T10:06:00Z", reported_at: "2026-11-07T10:06:01Z", flag: null },
      ],
      device_only: [{ call_id: "call-ccc", created_at: "2026-11-07T10:07:00Z" }],
    });
    renderPage(<GrantsTable />);

    const dialog = await openCalls();
    expect(api.grantCalls).toHaveBeenCalledWith("g-3");
    expect(await within(dialog).findByText("call-aaa")).toBeInTheDocument();
    expect(within(dialog).getByText("mail.send")).toBeInTheDocument();
    expect(within(dialog).getByText("Recorded by the gateway, not reported by the device")).toBeInTheDocument();
    expect(within(dialog).getByText("call-ccc")).toBeInTheDocument();
    expect(within(dialog).getByText(/shown as far as the grant recorded them/)).toBeInTheDocument();
  });

  it("shows a call's place, arguments, outcome and result, and the person's reason", async () => {
    api.grants.mockResolvedValue([BYPASS]);
    api.grantCalls.mockResolvedValue({
      data: [
        {
          id: "c-1",
          request_id: "r-1",
          call_id: "call-aaa",
          tool_id: "mail.send",
          side: "gateway",
          created_at: "2026-11-07T10:05:00Z",
          reported_at: null,
          flag: null,
          target: "C:\\Users\\anna\\report.docx",
          arguments: { to: "boss@example.com", subject: "Report" },
          outcome: "error",
          result: "Mailbox is full",
          result_at: "2026-11-07T10:05:02Z",
        },
      ],
      device_only: [],
      kind: "bypass",
      evidence: "shown",
      evidence_level: "results",
      reason: "Preparing the board meeting",
    });
    renderPage(<GrantsTable />);

    const dialog = await openCalls();
    expect(await within(dialog).findByText("Preparing the board meeting")).toBeInTheDocument();
    expect(within(dialog).getByText("C:\\Users\\anna\\report.docx")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Arguments").textContent).toContain('"to": "boss@example.com"');
    expect(within(dialog).getByText("Outcome: error")).toBeInTheDocument();
    expect(within(dialog).getByText("Mailbox is full")).toBeInTheDocument();
    expect(within(dialog).queryByText(/could not show the places/)).not.toBeInTheDocument();
  });

  it("says when the server could not show the places and arguments", async () => {
    api.grants.mockResolvedValue([BYPASS]);
    api.grantCalls.mockResolvedValue({ data: [], device_only: [], evidence: "unavailable" });
    renderPage(<GrantsTable />);

    const dialog = await openCalls();
    expect(await within(dialog).findByText(/could not show the places and arguments/)).toBeInTheDocument();
  });

  it("says the auditor role is needed when reading the calls is refused", async () => {
    api.grants.mockResolvedValue([BYPASS]);
    api.grantCalls.mockRejectedValue(new ApiError("forbidden", 403));
    renderPage(<GrantsTable />);

    const dialog = await openCalls();
    expect(await within(dialog).findByText(/needs the role approvals_auditor/)).toBeInTheDocument();
  });

  it("revokes one after confirming, then refreshes", async () => {
    renderPage(<GrantsTable />);
    await settled();

    await userEvent.click(screen.getAllByRole("button", { name: "Revoke" })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Revoke this grant?")).toBeInTheDocument();
    api.grants.mockResolvedValue([WORK]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(api.revokeGrant).toHaveBeenCalledWith("g-1"));
    await waitFor(() => expect(screen.queryByText("mail.send_draft")).not.toBeInTheDocument());
  });

  it("says so when the grant had ended meanwhile", async () => {
    api.revokeGrant.mockRejectedValue(new ApiError("not found", 404));
    renderPage(<GrantsTable />);
    await settled();

    await userEvent.click(screen.getAllByRole("button", { name: "Revoke" })[0]);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));

    expect(await screen.findByText("This grant had already ended.")).toBeInTheDocument();
  });
});
