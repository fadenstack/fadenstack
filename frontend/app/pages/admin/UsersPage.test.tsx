/**
 * An administrator suspends an account (after saying so) and lets it back in.
 * It could only be deleted from the console, with everything it owned.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import type { AdminUser } from "~/api/admin";
import { renderPage } from "~/test/renderPage";

import UsersPage from "./UsersPage";

vi.mock("~/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/admin")>();
  return {
    ...actual,
    adminUsers: {
      ...actual.adminUsers,
      list: vi.fn(),
      listRoles: vi.fn().mockResolvedValue([]),
      setActive: vi.fn(),
      setAdministrator: vi.fn(),
      handOver: vi.fn(),
      createResetLink: vi.fn(),
    },
    adminGroups: { ...actual.adminGroups, list: vi.fn().mockResolvedValue([]) },
  };
});

const users = vi.mocked(admin.adminUsers);

function person(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: "u-1",
    email: "pat@example.com",
    is_active: true,
    is_superuser: false,
    is_verified: true,
    usage_group_id: null,
    usage_group_name: null,
    developer_mode: false,
    developer_mode_until: null,
    roles: [],
    permissions: [],
    ...overrides,
  } as AdminUser;
}

describe("suspending an account", () => {
  beforeEach(() => {
    users.setActive.mockReset().mockResolvedValue(person({ is_active: false }));
  });

  it("asks first, then suspends", async () => {
    users.list.mockResolvedValue([person()]);
    const { container } = renderPage(<UsersPage />);
    await screen.findByText("pat@example.com");

    await userEvent.click(container.querySelector("[data-user-suspend='u-1']") as HTMLElement);
    expect(users.setActive).not.toHaveBeenCalled();
    expect(screen.getByText(/Nothing of theirs is deleted/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Suspend" }));
    await waitFor(() => expect(users.setActive).toHaveBeenCalledWith("u-1", false));
  });

  it("lets a suspended account back in", async () => {
    users.list.mockResolvedValue([person({ is_active: false })]);
    const { container } = renderPage(<UsersPage />);
    await screen.findByText("pat@example.com");

    await userEvent.click(container.querySelector("[data-user-reactivate='u-1']") as HTMLElement);
    await waitFor(() => expect(users.setActive).toHaveBeenCalledWith("u-1", true));
  });
});

describe("rights: administrator and auditor", () => {
  beforeEach(() => {
    users.setAdministrator.mockReset().mockResolvedValue(person({ is_superuser: true }));
    users.handOver.mockReset().mockResolvedValue(person({ id: "u-2", is_superuser: true }));
  });

  it("grants the administrator right only with a reason", async () => {
    users.list.mockResolvedValue([person()]);
    const { container } = renderPage(<UsersPage />);
    await screen.findByText("pat@example.com");

    await userEvent.click(container.querySelector("[data-user-rights='u-1']") as HTMLElement);
    const grant = await screen.findByRole("button", { name: "Make administrator" });
    expect(grant).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Reason/), "Taking over the night shift");
    await userEvent.click(grant);
    await waitFor(() =>
      expect(users.setAdministrator).toHaveBeenCalledWith("u-1", true, "Taking over the night shift"),
    );
  });

  it("hands the administrator right to someone else in one step", async () => {
    users.list.mockResolvedValue([
      person({ is_superuser: true }),
      person({ id: "u-2", email: "sam@example.com" }),
    ]);
    const { container } = renderPage(<UsersPage />);
    await screen.findByText("sam@example.com");

    await userEvent.click(container.querySelector("[data-user-rights='u-1']") as HTMLElement);
    await userEvent.type(await screen.findByLabelText(/Reason/), "Pat leaves the company");
    await userEvent.click(screen.getByLabelText("To"));
    await userEvent.click(await screen.findByRole("option", { name: "sam@example.com" }));
    await userEvent.click(screen.getByRole("button", { name: "Hand over" }));
    await waitFor(() =>
      expect(users.handOver).toHaveBeenCalledWith("u-1", "administrator", "u-2", "Pat leaves the company"),
    );
  });
});

describe("a password reset link", () => {
  it("is made only with a reason, and shown once with the token in the fragment", async () => {
    users.list.mockResolvedValue([person()]);
    users.createResetLink.mockResolvedValue({ token: "the-token-0123456789", expires_at: "2026-10-25T21:00:00Z" });
    const { container } = renderPage(<UsersPage />);
    await screen.findByText("pat@example.com");
    await userEvent.click(container.querySelector("[data-user-reset-link='u-1']") as HTMLElement);
    expect(screen.getByRole("button", { name: "Create link" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Reason/), "Pat lost the password");
    await userEvent.click(screen.getByRole("button", { name: "Create link" }));
    expect(users.createResetLink).toHaveBeenCalledWith("u-1", "Pat lost the password", 1);
    const url = (await screen.findByTestId("reset-link-url")) as HTMLInputElement;
    expect(url.value).toBe(`${window.location.origin}/reset-password#link=the-token-0123456789`);
    expect(screen.getByTestId("reset-link-made")).toHaveTextContent("works once");
  });

  it("says why the server refused it", async () => {
    users.list.mockResolvedValue([person()]);
    users.createResetLink.mockRejectedValue(new Error("Start Root Mode first: a password reset link is recorded with its reason."));
    const { container } = renderPage(<UsersPage />);
    await screen.findByText("pat@example.com");
    await userEvent.click(container.querySelector("[data-user-reset-link='u-1']") as HTMLElement);
    await userEvent.type(screen.getByLabelText(/^Reason/), "Pat lost the password");
    await userEvent.click(screen.getByRole("button", { name: "Create link" }));
    expect(await screen.findByText(/Start Root Mode first/)).toBeInTheDocument();
    expect(screen.queryByTestId("reset-link-url")).not.toBeInTheDocument();
  });

  it("is not offered for a suspended account", async () => {
    users.list.mockResolvedValue([person({ is_active: false })]);
    const { container } = renderPage(<UsersPage />);
    await screen.findByText("pat@example.com");
    expect(container.querySelector("[data-user-reset-link='u-1']")).toBeNull();
  });
});
