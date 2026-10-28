/** The account layout opens a person's own pages once they are signed in, whichever modules are on. */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useOutletContext } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as admin from "~/api/admin";
import * as authApi from "~/api/auth";

import AccountLayout from "./account";

vi.mock("~/lib/userLanguage", () => ({ applyUserLanguage: vi.fn() }));

function Page() {
  const { permissions } = useOutletContext<{ permissions: Set<string> }>();
  return <p>own page {[...permissions].join(",")}</p>;
}

function view() {
  return render(
    <MemoryRouter initialEntries={["/usage"]}>
      <Routes>
        <Route element={<AccountLayout />}>
          <Route path="/usage" element={<Page />} />
        </Route>
        <Route path="/login" element={<p>sign in</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("account layout", () => {
  it("shows the page to a signed-in person, with what they may do", async () => {
    vi.spyOn(authApi.auth, "me").mockResolvedValue({ id: "u-1", email: "a@example.com", is_superuser: false } as authApi.AuthUser);
    vi.spyOn(admin.adminUsers, "meAccess").mockResolvedValue({
      id: "u-1",
      email: "a@example.com",
      is_superuser: false,
      roles: [],
      permissions: [{ id: "p", resource: "usage", action: "read" }],
    });
    view();

    expect(await screen.findByText("own page usage:read")).toBeInTheDocument();
  });

  it("sends someone not signed in to sign in", async () => {
    vi.spyOn(authApi.auth, "me").mockRejectedValue(new Error("401"));
    view();

    await waitFor(() => expect(screen.getByText("sign in")).toBeInTheDocument());
  });
});
