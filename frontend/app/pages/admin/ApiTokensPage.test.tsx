/**
 * Access Control → API tokens: everyone's tokens, who owns each, how it was used, and revoking one.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import type { AdminApiTokenInfo } from "~/api/admin";
import { renderPage } from "~/test/renderPage";

import ApiTokensPage from "./ApiTokensPage";

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return {
    ...actual,
    adminUsers: {
      ...actual.adminUsers,
      listAllApiTokens: vi.fn(),
      listApiTokenUses: vi.fn(),
      revokeUserApiToken: vi.fn(),
    },
  };
});

const users = vi.mocked(admin.adminUsers);

function token(overrides: Partial<AdminApiTokenInfo> = {}): AdminApiTokenInfo {
  return {
    id: "t-1",
    name: "nightly export",
    tenant_id: "default",
    created_at: "2026-10-01T08:00:00Z",
    expires_at: "2099-01-01T00:00:00Z",
    revoked_at: null,
    last_used_at: "2026-10-26T21:00:00Z",
    last_used_address: "10.0.0.7",
    last_used_client: "faden-sdk/0.2",
    last_used_route: "POST /v1/chat/completions",
    recent_uses: 12,
    user_id: "u-1",
    email: "lina@example.com",
    ...overrides,
  };
}

describe("the API tokens overview", () => {
  beforeEach(() => {
    users.revokeUserApiToken.mockReset().mockResolvedValue(undefined);
    users.listApiTokenUses.mockReset().mockResolvedValue([
      { at: "2026-10-26T21:00:00Z", address: "10.0.0.7", client: "faden-sdk/0.2", route: "POST /v1/chat/completions" },
    ]);
  });

  it("shows each active token's owner and last use, and the ended ones on request", async () => {
    users.listAllApiTokens.mockResolvedValue([
      token(),
      token({ id: "t-2", name: "old script", revoked_at: "2026-10-20T00:00:00Z", last_used_at: null }),
    ]);
    renderPage(<ApiTokensPage />);
    await screen.findByText("lina@example.com");
    expect(screen.getByText(/10\.0\.0\.7/)).toBeTruthy();
    expect(screen.getByText(/faden-sdk\/0\.2/)).toBeTruthy();
    expect(screen.queryByText("old script")).toBeNull();
    await userEvent.click(screen.getByLabelText("Show revoked and expired"));
    expect(await screen.findByText("old script")).toBeTruthy();
  });

  it("lists a token's uses", async () => {
    users.listAllApiTokens.mockResolvedValue([token()]);
    renderPage(<ApiTokensPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Uses" }));
    await waitFor(() => expect(users.listApiTokenUses).toHaveBeenCalledWith("t-1"));
    expect(await screen.findByText("POST /v1/chat/completions")).toBeTruthy();
  });

  it("asks first, then revokes", async () => {
    users.listAllApiTokens.mockResolvedValue([token()]);
    renderPage(<ApiTokensPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    expect(users.revokeUserApiToken).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(users.revokeUserApiToken).toHaveBeenCalledWith("u-1", "t-1"));
  });
});
