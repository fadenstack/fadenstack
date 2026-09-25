/**
 * Registering and editing an MCP server from the console.
 *
 * The pages sent the database's column names (`command_json`), which the
 * service ignores, and had nowhere to put a header or an env var: GitHub's
 * hosted server could not be given its token, and a stdio server could not be
 * registered at all (release VM, 2026-09-25). Headers and env are write-only:
 * what is set shows by name, and an edit sends only what changed.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as mcp from "~/api/mcp";
import type { MCPServerDetail } from "~/api/mcp";
import { renderPage } from "~/test/renderPage";

import MCPServerDetailPage from "./MCPServerDetailPage";
import MCPServersPage from "./MCPServersPage";
import { NO_EDITS, toSecretMap, toSecretPatch } from "./SecretPairsEditor";
import { serverPatch, type EditForm } from "./serverForm";

vi.mock("~/api/mcp", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/api/mcp")>()),
  listServers: vi.fn(),
  registerServer: vi.fn(),
  getServer: vi.fn(),
  listServerTools: vi.fn(),
  updateServer: vi.fn(),
  getSettingsSchema: vi.fn(),
}));

const api = vi.mocked(mcp);

beforeEach(() => {
  vi.clearAllMocks();
});

const GITHUB: MCPServerDetail = {
  id: "s1",
  name: "github",
  transport: "streamable_http",
  tool_prefix: "gh",
  status: "error",
  pii_mode: "allow",
  enabled: true,
  discovered_tools: 0,
  has_settings: false,
  created_at: "2026-09-25T20:00:00Z",
  updated_at: "2026-09-25T20:00:00Z",
  last_discovery_at: null,
  last_error:
    "https://api.githubcopilot.com/mcp answered 401 Unauthorized: add an Authorization: Bearer <token> header.",
  url: "https://api.githubcopilot.com/mcp",
  command: null,
  args: null,
  working_dir: null,
  timeout_sec: 60,
  heartbeat_interval_sec: 30,
  tenant_id: "default",
  header_names: ["Authorization"],
  env_names: [],
};

const TIME: MCPServerDetail = {
  ...GITHUB,
  id: "s2",
  name: "time",
  transport: "stdio",
  tool_prefix: "time",
  url: null,
  command: ["uvx", "mcp-server-time"],
  args: ["--local-timezone", "UTC"],
  header_names: [],
  env_names: ["TZ"],
  last_error: null,
};

/** Paste, not type: a keystroke per character re-renders the whole page. */
async function fill(user: ReturnType<typeof userEvent.setup>, field: HTMLElement, text: string) {
  await user.click(field);
  await user.paste(text);
}

function form(server: MCPServerDetail, change: Partial<EditForm> = {}): EditForm {
  return {
    url: server.url ?? "",
    command: [...(server.command ?? []), ...(server.args ?? [])].join(" "),
    timeoutSec: server.timeout_sec,
    piiMode: server.pii_mode,
    headers: NO_EDITS,
    env: NO_EDITS,
    ...change,
  };
}

describe("secret edits", () => {
  it("set a key with a value, remove one with null, skip a blank row", () => {
    const edits = {
      pairs: [
        { name: " Authorization ", value: "Bearer t0ken" },
        { name: "X-Empty", value: "" },
        { name: "", value: "orphan" },
      ],
      removed: ["X-Old"],
    };
    expect(toSecretPatch(edits)).toEqual({ Authorization: "Bearer t0ken", "X-Old": null });
    expect(toSecretMap(edits)).toEqual({ Authorization: "Bearer t0ken" });
  });
});

describe("serverPatch", () => {
  it("sends nothing when nothing changed", () => {
    expect(serverPatch(GITHUB, form(GITHUB))).toEqual({});
    expect(serverPatch(TIME, form(TIME))).toEqual({});
  });

  it("sends a new token without the unchanged URL", () => {
    const headers = { pairs: [{ name: "Authorization", value: "Bearer new" }], removed: [] };
    expect(serverPatch(GITHUB, form(GITHUB, { headers }))).toEqual({
      headers: { Authorization: "Bearer new" },
    });
  });

  it("sends a changed command whole, and empties args so nothing repeats", () => {
    const patch = serverPatch(TIME, form(TIME, { command: "uvx mcp-server-time" }));
    expect(patch).toEqual({ command: ["uvx", "mcp-server-time"], args: [] });
  });

  it("sends env for stdio and never headers", () => {
    const env = { pairs: [], removed: ["TZ"] };
    const headers = { pairs: [{ name: "Authorization", value: "x" }], removed: [] };
    expect(serverPatch(TIME, form(TIME, { env, headers }))).toEqual({ env: { TZ: null } });
  });
});

describe("errorMessage", () => {
  it("reads FastAPI's detail, a validation list, or plain text", () => {
    expect(mcp.errorMessage('{"detail":"Tool prefix \'gh\' already exists."}')).toBe(
      "Tool prefix 'gh' already exists.",
    );
    expect(
      mcp.errorMessage('{"detail":[{"msg":"field required"},{"msg":"too short"}]}'),
    ).toBe("field required; too short");
    expect(mcp.errorMessage("Bad Gateway")).toBe("Bad Gateway");
  });
});

// A whole page and a dialog in jsdom: seconds each, more under a parallel run.
const PAGE_TEST_MS = 20_000;

describe("register dialog", () => {
  beforeEach(() => {
    api.listServers.mockResolvedValue([]);
  });

  it("sends the URL, the header and the timeout under the service's names", async () => {
    api.registerServer.mockResolvedValue({ ...GITHUB, warnings: [] });
    const user = userEvent.setup();
    renderPage(<MCPServersPage />);

    await user.click(await screen.findByRole("button", { name: "Register Server" }));
    const dialog = await screen.findByRole("dialog");
    await fill(user, within(dialog).getByLabelText(/Server Name/), "github");
    await fill(
      user,
      within(dialog).getByLabelText(/Server URL/),
      "https://api.githubcopilot.com/mcp",
    );
    await user.click(within(dialog).getByRole("button", { name: "Add header" }));
    await fill(user, within(dialog).getByLabelText("Name"), "Authorization");
    await fill(user, within(dialog).getByLabelText("Value"), "Bearer t0ken");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));

    await waitFor(() => expect(api.registerServer).toHaveBeenCalledTimes(1));
    const payload = api.registerServer.mock.calls[0][0];
    expect(payload).toMatchObject({
      name: "github",
      transport: "streamable_http",
      url: "https://api.githubcopilot.com/mcp",
      headers: { Authorization: "Bearer t0ken" },
      timeout_sec: 60,
    });
    expect(payload).not.toHaveProperty("command_json");
    expect(payload).not.toHaveProperty("env");
  }, PAGE_TEST_MS);

  it("registers a stdio server with a command and env", async () => {
    api.registerServer.mockResolvedValue({ ...TIME, warnings: [] });
    const user = userEvent.setup();
    renderPage(<MCPServersPage />);

    await user.click(await screen.findByRole("button", { name: "Register Server" }));
    const dialog = await screen.findByRole("dialog");
    await fill(user, within(dialog).getByLabelText(/Server Name/), "time");
    await user.click(within(dialog).getByLabelText(/Transport/));
    await user.click(await screen.findByRole("option", { name: "Stdio (command)" }));
    await fill(user, within(dialog).getByLabelText(/Command/), "uvx mcp-server-time");
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));
    await fill(user, within(dialog).getByLabelText("Name"), "TZ");
    await fill(user, within(dialog).getByLabelText("Value"), "UTC");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));

    await waitFor(() => expect(api.registerServer).toHaveBeenCalledTimes(1));
    expect(api.registerServer.mock.calls[0][0]).toMatchObject({
      transport: "stdio",
      command: ["uvx", "mcp-server-time"],
      env: { TZ: "UTC" },
    });
  }, PAGE_TEST_MS);

  it("says why a registered server could not be connected", async () => {
    api.registerServer.mockResolvedValue({
      ...GITHUB,
      warnings: ["https://api.githubcopilot.com/mcp answered 401 Unauthorized."],
    });
    const user = userEvent.setup();
    renderPage(<MCPServersPage />);

    await user.click(await screen.findByRole("button", { name: "Register Server" }));
    const dialog = await screen.findByRole("dialog");
    await fill(user, within(dialog).getByLabelText(/Server Name/), "github");
    await fill(user, within(dialog).getByLabelText(/Server URL/), "https://x.test/mcp");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));

    expect(await screen.findByText(/is registered but could not be connected/)).toHaveTextContent(
      "401 Unauthorized",
    );
  }, PAGE_TEST_MS);

  it("keeps a rejected registration in the dialog, with the service's reason", async () => {
    api.registerServer.mockRejectedValue(new Error("Tool prefix 'gh' already exists for tenant."));
    const user = userEvent.setup();
    renderPage(<MCPServersPage />);

    await user.click(await screen.findByRole("button", { name: "Register Server" }));
    const dialog = await screen.findByRole("dialog");
    await fill(user, within(dialog).getByLabelText(/Server Name/), "github");
    await fill(user, within(dialog).getByLabelText(/Server URL/), "https://x.test/mcp");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));

    expect(await within(dialog).findByText(/already exists for tenant/)).toBeInTheDocument();
  }, PAGE_TEST_MS);
});

describe("server detail", () => {
  const ROUTE = "/admin/mcp/servers/:id";

  beforeEach(() => {
    api.listServerTools.mockResolvedValue([]);
  });

  it("shows why the last connect failed, the header names, and no settings panel", async () => {
    api.getServer.mockResolvedValue(GITHUB);
    renderPage(<MCPServerDetailPage />, { path: ROUTE, initialPath: "/admin/mcp/servers/s1" });

    expect(await screen.findByText("The last connection attempt failed")).toBeInTheDocument();
    expect(screen.getByText(/answered 401 Unauthorized/)).toBeInTheDocument();
    expect(screen.getByText("Authorization")).toBeInTheDocument();
    expect(api.getSettingsSchema).not.toHaveBeenCalled();
  }, PAGE_TEST_MS);

  it("removes a stored header without asking for the others", async () => {
    api.getServer.mockResolvedValue(GITHUB);
    api.updateServer.mockResolvedValue({ ...GITHUB, header_names: [] });
    const user = userEvent.setup();
    renderPage(<MCPServerDetailPage />, { path: ROUTE, initialPath: "/admin/mcp/servers/s1" });

    await screen.findByText("github");
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.updateServer).toHaveBeenCalledWith("s1", { headers: { Authorization: null } }),
    );
  }, PAGE_TEST_MS);

  it("replaces a stored token", async () => {
    api.getServer.mockResolvedValue(GITHUB);
    api.updateServer.mockResolvedValue(GITHUB);
    const user = userEvent.setup();
    renderPage(<MCPServerDetailPage />, { path: ROUTE, initialPath: "/admin/mcp/servers/s1" });

    await screen.findByText("github");
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Replace" }));
    await fill(user, within(dialog).getByLabelText("Value"), "Bearer n3w");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.updateServer).toHaveBeenCalledWith("s1", {
        headers: { Authorization: "Bearer n3w" },
      }),
    );
  }, PAGE_TEST_MS);
});
