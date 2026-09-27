/**
 * Editing a container: the create form, filled with what it was created with.
 *
 * The console could not edit a container at all: a running one could only be
 * deleted and made again by hand. Saving replaces it (Docker changes none of
 * these settings in a container that exists), after a confirmation in the
 * page rather than the browser's own dialog.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import type { ContainerSettings } from "~/api/admin";
import { renderPage } from "~/test/renderPage";

import CreateContainerPage from "./CreateContainerPage";

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return {
    ...actual,
    containers: {
      ...actual.containers,
      settings: vi.fn(),
      update: vi.fn(),
      create: vi.fn(),
    },
    networks: { ...actual.networks, list: vi.fn().mockResolvedValue([]) },
    images: { ...actual.images, list: vi.fn().mockResolvedValue([]) },
  };
});

const api = vi.mocked(admin.containers);

const EVERYTHING: ContainerSettings = {
  image: "node:22-alpine",
  name: "mcp-everything",
  cmd: ["npx", "-y", "@modelcontextprotocol/server-everything", "streamableHttp"],
  env: ["PORT=3001", "GREETING=hello world"],
  ports: [],
  volumes: ["/srv/data:/data:ro"],
  network: "fadenstack_default",
  running: true,
  container_class: "MCP",
  owner_scope: "platform",
  policy: "free",
  managed_by_compose: false,
  backend_runs_here: false,
};

function editPage(settings: ContainerSettings) {
  api.settings.mockResolvedValue(settings);
  renderPage(<CreateContainerPage />, {
    path: "/admin/containers/:id/edit",
    initialPath: "/admin/containers/old-id/edit",
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("editing a container", () => {
  it("fills the form, asks before replacing, and sends what it was created with", async () => {
    api.update.mockResolvedValue({ id: "new-id" } as admin.ContainerSummary);
    const user = userEvent.setup();
    editPage(EVERYTHING);

    expect(await screen.findByText("Edit mcp-everything")).toBeInTheDocument();
    expect(screen.getByDisplayValue("GREETING")).toBeInTheDocument();
    expect(screen.getByDisplayValue("hello world")).toBeInTheDocument();
    expect(screen.getByDisplayValue("/data:ro")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save and replace" }));
    expect(api.update).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "Replace" }));

    await waitFor(() => expect(api.update).toHaveBeenCalledOnce());
    const [id, payload] = api.update.mock.calls[0];
    expect(id).toBe("old-id");
    expect(payload).toMatchObject({
      image: "node:22-alpine",
      name: "mcp-everything",
      container_class: "MCP",
      auto_start: true,
      cmd: ["npx", "-y", "@modelcontextprotocol/server-everything", "streamableHttp"],
      env: ["PORT=3001", "GREETING=hello world"],
      volumes: ["/srv/data:/data:ro"],
      network: "fadenstack_default",
    });
  });

  it("does not offer to replace the container the backend runs in", async () => {
    editPage({ ...EVERYTHING, name: "faden-backend", backend_runs_here: true });

    expect(await screen.findByText(/cannot replace it/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save and replace" })).toBeDisabled();
  });

  it("says that Fadenstack's own containers come back from the compose file", async () => {
    editPage({ ...EVERYTHING, managed_by_compose: true });
    expect(await screen.findByText(/next faden deploy or upgrade/)).toBeInTheDocument();
  });
});

describe("creating a container", () => {
  it("starts as a container of one's own, which the console can start and stop", async () => {
    renderPage(<CreateContainerPage />, { path: "/admin/containers/new" });
    expect(await screen.findByRole("button", { name: /TENANT_APP/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^MCP$/ })).toBeInTheDocument();
  });
});
