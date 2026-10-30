/**
 * Groups as a tree: the list in tree order, the parent picker without cycles, the cost centre, archiving
 * and bringing back, and the tree's refusals in words.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import type { Group } from "~/api/admin";
import { ApiError } from "~/api/http";
import * as teams from "~/api/teamAdmin";
import { renderPage } from "~/test/renderPage";

import GroupsPage from "./GroupsPage";

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return {
    ...actual,
    adminGroups: { ...actual.adminGroups, list: vi.fn() },
    adminUsers: { ...actual.adminUsers, list: vi.fn().mockResolvedValue([]), listRoles: vi.fn().mockResolvedValue([]) },
    adminAuthProviders: { ...actual.adminAuthProviders, listPublic: vi.fn().mockResolvedValue([]) },
  };
});

vi.mock("~/api/teamAdmin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/teamAdmin")>();
  return {
    ...actual,
    teamAdmin: { create: vi.fn(), update: vi.fn(), archive: vi.fn(), unarchive: vi.fn() },
  };
});

const list = vi.mocked(admin.adminGroups.list);
const write = vi.mocked(teams.teamAdmin);

function group(id: string, overrides: Partial<Group> = {}): Group {
  return {
    id,
    name: id,
    description: null,
    created_at: "2026-10-01T00:00:00Z",
    roles: [],
    member_count: 3,
    parent_id: null,
    cost_center: null,
    archived_at: null,
    ...overrides,
  };
}

const TREE = [
  group("Sales", { cost_center: "4711" }),
  group("EMEA", { parent_id: "Sales" }),
  group("Berlin", { parent_id: "EMEA" }),
  group("Engineering"),
  group("Old", { archived_at: "2026-09-01T00:00:00Z" }),
];

function order(container: HTMLElement): string[] {
  return [...container.querySelectorAll("[data-team-row]")].map(
    (el) => `${el.getAttribute("data-team-row")}:${el.getAttribute("data-team-depth")}`,
  );
}

beforeEach(() => {
  list.mockResolvedValue(TREE);
  write.update.mockReset().mockResolvedValue(group("EMEA"));
  write.archive.mockReset().mockResolvedValue(group("Engineering"));
  write.unarchive.mockReset().mockResolvedValue(group("Old"));
});

describe("groups as a tree", () => {
  it("lists sub-groups below their parent, archived groups only on demand", async () => {
    const { container } = renderPage(<GroupsPage />);
    await screen.findByText("Berlin");

    expect(order(container)).toEqual(["Engineering:1", "Sales:1", "EMEA:2", "Berlin:3"]);
    expect(screen.getByText("Cost centre 4711")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Show 1 archived group"));
    expect(order(container)).toContain("Old:1");
    expect(screen.getByText("Archived")).toBeInTheDocument();
  });

  it("offers no parent that would close a cycle, and saves the parent and the cost centre", async () => {
    const { container } = renderPage(<GroupsPage />);
    await screen.findByText("Berlin");

    const row = container.querySelector("[data-team-row='EMEA']")!.closest("tr")!;
    await userEvent.click(within(row).getByTitle("Edit Group"));
    const dialog = await screen.findByRole("dialog");

    await userEvent.click(within(dialog).getByRole("combobox", { name: "Parent" }));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent ?? "");
    expect(options.some((o) => o.startsWith("Engineering"))).toBe(true);
    expect(options.some((o) => o.startsWith("Berlin"))).toBe(false); // its own sub-group
    expect(options.some((o) => o.startsWith("EMEA"))).toBe(false); // itself
    expect(options.some((o) => o.startsWith("Old"))).toBe(false); // archived
    await userEvent.click(screen.getByRole("option", { name: /^Engineering/ }));

    await userEvent.type(within(dialog).getByRole("textbox", { name: "Cost centre" }), "EU-1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(write.update).toHaveBeenCalledWith("EMEA", expect.objectContaining({ parent_id: "Engineering", cost_center: "EU-1" })),
    );
  });

  it("keeps a sub-group of an archived group below it, marked, and lets it keep that parent", async () => {
    list.mockResolvedValue([...TREE, group("Legacy", { parent_id: "Old" })]);
    const { container } = renderPage(<GroupsPage />);
    await screen.findByText("Legacy");

    expect(order(container)).toEqual(["Engineering:1", "Old:1", "Legacy:2", "Sales:1", "EMEA:2", "Berlin:3"]);
    const old = container.querySelector("[data-team-row='Old']") as HTMLElement;
    expect(within(old).getByText("Archived")).toBeInTheDocument();

    const row = container.querySelector("[data-team-row='Legacy']")!.closest("tr")!;
    await userEvent.click(within(row).getByTitle("Edit Group"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("combobox", { name: "Parent" })).toHaveTextContent("Old");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(write.update).toHaveBeenCalledWith("Legacy", expect.objectContaining({ parent_id: "Old" })));
  });

  it("words the tree's refusals", async () => {
    write.update.mockRejectedValue(new ApiError("unprocessable", 422, "team_too_deep"));
    const { container } = renderPage(<GroupsPage />);
    await screen.findByText("Berlin");

    const row = container.querySelector("[data-team-row='Engineering']")!.closest("tr")!;
    await userEvent.click(within(row).getByTitle("Edit Group"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText(/The tree would have more than six levels/)).toBeInTheDocument();
  });

  it("archives after saying what it means, and brings a group back", async () => {
    const { container } = renderPage(<GroupsPage />);
    await screen.findByText("Berlin");

    await userEvent.click(container.querySelector("[data-team-archive='Engineering']") as HTMLElement);
    expect(write.archive).not.toHaveBeenCalled();
    expect(screen.getByText(/keeps its history/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(write.archive).toHaveBeenCalledWith("Engineering"));

    await userEvent.click(screen.getByLabelText("Show 1 archived group"));
    await userEvent.click(container.querySelector("[data-team-unarchive='Old']") as HTMLElement);
    await waitFor(() => expect(write.unarchive).toHaveBeenCalledWith("Old"));
  });
});
