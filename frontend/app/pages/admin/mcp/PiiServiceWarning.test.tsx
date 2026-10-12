/**
 * Redact and Block refuse every call while the PII service is off, so the
 * console says so beside the mode, and a new server starts on Allow there.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as mcp from "~/api/mcp";
import { useServices } from "~/lib/ServicesContext";
import { renderPage } from "~/test/renderPage";

import MCPServersPage from "./MCPServersPage";
import { PiiServiceWarning } from "./PiiServiceWarning";

vi.mock("~/api/mcp", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/api/mcp")>()),
  listServers: vi.fn(),
  registerServer: vi.fn(),
}));

vi.mock("~/lib/ServicesContext", () => ({ useServices: vi.fn() }));

const api = vi.mocked(mcp);
const services = vi.mocked(useServices);

function piiModule(enabled: boolean, loading = false) {
  services.mockReturnValue({
    services: [],
    loading,
    error: null,
    isModuleEnabled: (name: string) => name === "pii" && enabled,
    getModule: () => undefined,
    refresh: async () => {},
  });
}

const WARNING = /personal-data check cannot run/;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PiiServiceWarning", () => {
  it.each(["redact", "block"] as const)("warns in %s mode while the PII module is off", (mode) => {
    piiModule(false);
    render(<PiiServiceWarning mode={mode} />);
    expect(screen.getByText(WARNING)).toHaveTextContent("set the PII mode to Allow");
  });

  it("says nothing in allow mode", () => {
    piiModule(false);
    render(<PiiServiceWarning mode="allow" />);
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("says nothing while the PII module runs", () => {
    piiModule(true);
    render(<PiiServiceWarning mode="redact" />);
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("says nothing while the module list is still loading", () => {
    piiModule(false, true);
    render(<PiiServiceWarning mode="redact" />);
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });
});

describe("register dialog without the PII module", () => {
  it("starts on Allow, and warns once Redact is chosen", async () => {
    piiModule(false);
    api.listServers.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage(<MCPServersPage />);

    await user.click(await screen.findByRole("button", { name: "Register Server" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(WARNING)).not.toBeInTheDocument();

    await user.click(within(dialog).getByLabelText(/PII Mode/));
    await user.click(await screen.findByRole("option", { name: /^Redact/ }));
    await waitFor(() => expect(within(dialog).getByText(WARNING)).toBeInTheDocument());
  }, 20_000);

  it("starts on Redact when the PII module runs", async () => {
    piiModule(true);
    api.listServers.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage(<MCPServersPage />);

    await user.click(await screen.findByRole("button", { name: "Register Server" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/PII Mode/)).toHaveTextContent(/^Redact/);
    expect(within(dialog).queryByText(WARNING)).not.toBeInTheDocument();
  }, 20_000);
});
