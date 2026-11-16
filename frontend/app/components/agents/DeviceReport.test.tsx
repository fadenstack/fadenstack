/** A device's report: system and version, plugins and MCP programs with shortened hashes, and the empty cases. */
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentDevice } from "~/api/agents";
import { getAppTheme } from "~/theme";

import { DeviceReport } from "./DeviceReport";

const PLUGIN = "0123456789abcdef".repeat(4);
const MCP = "fedcba9876543210".repeat(4);

function device(overrides: Partial<AgentDevice> = {}): AgentDevice {
  return {
    os: "Windows 11",
    app_version: "0.7.1",
    plugins: [{ name: "excel-tools", version: "1.2", sha256: PLUGIN }],
    mcp_programs: [{ name: "files", program: "files-mcp.exe", sha256: MCP }],
    reported_at: "2026-10-02T09:00:00Z",
    ...overrides,
  };
}

const show = (d: AgentDevice | null) =>
  render(
    <ThemeProvider theme={getAppTheme("light")}>
      <DeviceReport device={d} />
    </ThemeProvider>,
  );

afterEach(() => vi.restoreAllMocks());

describe("DeviceReport", () => {
  it("shows system, version, plugins and MCP programs with shortened hashes", () => {
    show(device());
    expect(screen.getByText("System:")).toBeInTheDocument();
    expect(screen.getByText("Windows 11", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("App version:")).toBeInTheDocument();
    expect(screen.getByText("0.7.1", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Plugins" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "MCP programs" })).toBeInTheDocument();
    expect(screen.getByText("excel-tools")).toBeInTheDocument();
    expect(screen.getByText("1.2")).toBeInTheDocument();
    expect(screen.getByText("files-mcp.exe")).toBeInTheDocument();
    expect(screen.getByText(`${PLUGIN.slice(0, 12)}…`)).toBeInTheDocument();
    expect(screen.getByText(`${MCP.slice(0, 12)}…`)).toBeInTheDocument();
    expect(screen.queryByText(PLUGIN)).not.toBeInTheDocument();
    expect(screen.getByText(/Reported /)).toBeInTheDocument();
  });

  it("gives the whole hash in the tooltip text and copies it", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    show(device());
    expect(screen.getByLabelText(`SHA-256: ${PLUGIN}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy SHA-256 of excel-tools" }));
    expect(writeText).toHaveBeenCalledWith(PLUGIN);
  });

  it("says when nothing has been reported yet", () => {
    show(null);
    expect(screen.getByText("Not reported yet")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Plugins" })).not.toBeInTheDocument();
  });

  it("says so when the lists are empty and the system is unknown", () => {
    show(device({ os: null, app_version: null, plugins: [], mcp_programs: [] }));
    expect(screen.getByText("No plugins.")).toBeInTheDocument();
    expect(screen.getByText("No MCP programs.")).toBeInTheDocument();
    expect(screen.getAllByText("Unknown", { exact: false }).length).toBeGreaterThan(0);
  });
});
