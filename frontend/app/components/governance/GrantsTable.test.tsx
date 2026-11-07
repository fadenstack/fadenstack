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
  return { ...actual, governanceApi: { grants: vi.fn(), revokeGrant: vi.fn() } };
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
