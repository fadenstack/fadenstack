/** `/admin` leads each person to where the console opens for them. */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";

import AdminRouteRedirectPage from "./AdminRouteRedirectPage";

function view() {
  return render(
    <MemoryRouter initialEntries={["/admin"]}>
      <Routes>
        <Route path="/admin" element={<AdminRouteRedirectPage />} />
        <Route path="/admin/dashboard" element={<p>dashboard</p>} />
        <Route path="/admin/usage" element={<p>usage page</p>} />
        <Route path="/chat" element={<p>chat</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const access = (is_superuser: boolean, keys: [string, string][]) => ({
  id: "u-1",
  email: "a@example.com",
  is_superuser,
  roles: [],
  permissions: keys.map(([resource, action], i) => ({ id: `p${i}`, resource, action })),
});

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("/admin", () => {
  it("leads a superuser to the dashboard", async () => {
    vi.spyOn(admin.adminUsers, "meAccess").mockResolvedValue(access(true, []));
    view();
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });

  it("leads someone with usage:read to the Usage page", async () => {
    vi.spyOn(admin.adminUsers, "meAccess").mockResolvedValue(access(false, [["usage", "read"]]));
    view();
    expect(await screen.findByText("usage page")).toBeInTheDocument();
  });

  it("leads anyone else to the chat", async () => {
    vi.spyOn(admin.adminUsers, "meAccess").mockResolvedValue(access(false, [["chat", "read"]]));
    view();
    expect(await screen.findByText("chat")).toBeInTheDocument();
  });
});
