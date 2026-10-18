/**
 * A container's page: Fadenstack's own containers are edited only in Root Mode.
 *
 * A new image or environment for the gateway reads every chat with the key it
 * holds; the backend refuses the edit, and the page says so before the form.
 */
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import type { ContainerDetail } from "~/api/admin";
import { renderPage } from "~/test/renderPage";

import ContainerDetailPage from "./ContainerDetailPage";

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return { ...actual, containers: { ...actual.containers, get: vi.fn() } };
});

const api = vi.mocked(admin.containers);

function container(over: Partial<ContainerDetail>): ContainerDetail {
  return {
    id: "a".repeat(64),
    name: "faden-gateway",
    image: "ghcr.io/fadenstack/gateway:0.4.5",
    status: "Up 2 hours",
    state: "running",
    created: "2026-10-18T10:00:00Z",
    ports: [],
    networks: ["fadenstack_default"],
    endpoint: "local",
    container_class: "SYSTEM_AUX",
    policy: "free",
    owner_scope: "platform",
    ...over,
  };
}

async function editButton(detail: ContainerDetail, rootModeActive: boolean) {
  api.get.mockResolvedValue(detail);
  renderPage(<ContainerDetailPage />, {
    path: "/admin/containers/:id",
    initialPath: `/admin/containers/${detail.id}`,
    context: { rootModeActive },
  });
  return screen.findByRole("button", { name: /^Edit$/i });
}

describe("ContainerDetailPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does not offer to edit one of Fadenstack's own containers outside Root Mode", async () => {
    expect(await editButton(container({}), false)).toBeDisabled();
  });

  it("offers it in Root Mode", async () => {
    expect(await editButton(container({ container_class: "SYSTEM_CORE" }), true)).toBeEnabled();
  });

  it("offers a tenant's container without Root Mode", async () => {
    expect(await editButton(container({ name: "tool", container_class: "TENANT_APP" }), false)).toBeEnabled();
  });

  it("never offers a locked one", async () => {
    api.get.mockResolvedValue(container({ name: "tool", container_class: "TENANT_APP", policy: "locked" }));
    renderPage(<ContainerDetailPage />, {
      path: "/admin/containers/:id",
      initialPath: `/admin/containers/${"a".repeat(64)}`,
      context: { rootModeActive: true },
    });
    expect(await screen.findByText("tool")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit$/i })).toBeNull();
  });
});
