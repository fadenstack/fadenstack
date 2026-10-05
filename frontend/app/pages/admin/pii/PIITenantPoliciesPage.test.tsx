/**
 * The tenant PII policy page: besides the policy override, a switch that lets
 * chats and agents change the PII mode. It saves on its own, without writing
 * a policy override.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as pii from "~/api/pii";
import type { PIITenantPolicyPayload } from "~/api/pii";
import { renderPage } from "~/test/renderPage";

import PIITenantPoliciesPage from "./PIITenantPoliciesPage";

vi.mock("~/api/pii", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/pii")>();
  return {
    ...actual,
    fetchPIIPolicyOptions: vi.fn(),
    listPIIPolicyTenants: vi.fn(),
    getPIITenantPolicy: vi.fn(),
    upsertPIITenantPolicy: vi.fn(),
    clearPIITenantPolicy: vi.fn(),
    setPIITenantModeSwitch: vi.fn(),
  };
});

const api = vi.mocked(pii);

function tenant(overrides: Partial<PIITenantPolicyPayload> = {}): PIITenantPolicyPayload {
  return {
    tenant_id: "default",
    has_override: false,
    override_policy: null,
    default_policy: pii.normalizePIIPolicy(undefined),
    effective_policy: pii.normalizePIIPolicy(undefined),
    allow_mode_override: false,
    ...overrides,
  };
}

async function loadTenant() {
  renderPage(<PIITenantPoliciesPage />);
  await userEvent.type(screen.getByRole("textbox", { name: "Tenant ID" }), "default");
  await userEvent.click(screen.getByRole("button", { name: "Load Tenant" }));
  return screen.findByRole("switch", { name: "Let chats and agents change the PII mode" });
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchPIIPolicyOptions.mockRejectedValue(new Error("offline"));
  api.listPIIPolicyTenants.mockResolvedValue({ items: [] });
});

describe("PIITenantPoliciesPage", () => {
  it("shows the tenant's switch and saves it on its own", async () => {
    api.getPIITenantPolicy.mockResolvedValue(tenant());
    api.setPIITenantModeSwitch.mockResolvedValue(tenant({ allow_mode_override: true }));

    const toggle = await loadTenant();
    expect(toggle).not.toBeChecked();
    expect(screen.getByText(/Off: requests use this policy's mode/)).toBeInTheDocument();

    await userEvent.click(toggle);
    await waitFor(() => expect(api.setPIITenantModeSwitch).toHaveBeenCalledWith("default", true));
    expect(api.upsertPIITenantPolicy).not.toHaveBeenCalled();
    expect(await screen.findByText("Saved: whether chats and agents may change the PII mode.")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Let chats and agents change the PII mode" })).toBeChecked();
  });

  it("shows a tenant that allows it as on, and turns it off", async () => {
    api.getPIITenantPolicy.mockResolvedValue(tenant({ allow_mode_override: true }));
    api.setPIITenantModeSwitch.mockResolvedValue(tenant({ allow_mode_override: false }));

    const toggle = await loadTenant();
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    await waitFor(() => expect(api.setPIITenantModeSwitch).toHaveBeenCalledWith("default", false));
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Let chats and agents change the PII mode" })).not.toBeChecked(),
    );
  });
});
