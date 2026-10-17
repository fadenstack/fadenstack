/**
 * The audit trail holds e-mail and IP addresses: an auditor reads it without the logs, and viewers read the logs
 * without it.
 */
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderPage } from "~/test/renderPage";

import LogsPage from "./LogsPage";

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return {
    ...actual,
    audit: {
      ...actual.audit,
      list: vi.fn().mockResolvedValue([
        {
          id: "e1", time: "2026-10-18T08:00:00Z", actor_id: "u1", actor_email: "pat@example.com",
          action: "user.roles.set", target_type: "user", target_id: "u2", result: "allow", severity: "high",
          metadata_json: null, client_ip: "10.1.2.3", request_id: "r1", user_agent: null, source: "console",
          hash: "h",
        },
      ]),
      chain: vi.fn().mockResolvedValue({ ok: true, events: 1, legacy_rows: 0, intact_until: null, problems: [] }),
    },
  };
});

vi.mock("~/api/logs", () => ({ logsApi: {} }));

describe("who reads the audit trail", () => {
  it("shows an auditor the trail alone, with the person's e-mail and the chain's state", async () => {
    renderPage(<LogsPage />, { context: { permissions: new Set(["audit:read"]) } });
    expect(await screen.findByText("pat@example.com")).toBeInTheDocument();
    expect(screen.getByText(/Intact: every event is as it was written/)).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Export CSV/ })).not.toBeInTheDocument();
  });
});
