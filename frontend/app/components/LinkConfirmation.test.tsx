/**
 * The owner of an account confirms that an outside sign-in which met it is theirs (backend
 * services/sign_in/link_requests.py): opening the link changes nothing, confirming links once.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import * as admin from "~/api/admin";
import { renderPage } from "~/test/renderPage";
import Login from "~/routes/login";

import { LinkConfirmation } from "./LinkConfirmation";

// Made before each test: the config restores spies between tests (vitest.config.ts).
let linkPreview: MockInstance<typeof admin.adminAuthProviders.linkPreview>;
let linkConfirm: MockInstance<typeof admin.adminAuthProviders.linkConfirm>;
const PREVIEW = {
  provider_name: "Corp", identity_email: "ada@corp.example", account_email: "ada@example.com",
  expires_at: "2026-10-25T13:00:00Z",
};

beforeEach(() => {
  linkPreview = vi.spyOn(admin.adminAuthProviders, "linkPreview");
  linkConfirm = vi.spyOn(admin.adminAuthProviders, "linkConfirm");
});

describe("confirming a link", () => {
  it("shows what would be linked and links only when confirmed", async () => {
    linkPreview.mockResolvedValue(PREVIEW);
    linkConfirm.mockResolvedValue();
    render(<LinkConfirmation token="the-token-from-the-mail" />);
    const asking = await screen.findByTestId("link-confirmation");
    expect(asking).toHaveTextContent("Corp signed in as ada@corp.example");
    expect(asking).toHaveTextContent("ada@example.com");
    expect(linkPreview).toHaveBeenCalledWith("the-token-from-the-mail");
    expect(linkConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Link it" }));
    expect(await screen.findByTestId("link-done")).toHaveTextContent("You can now sign in with Corp");
    expect(linkConfirm).toHaveBeenCalledWith("the-token-from-the-mail");
  });

  it("says so when the link was used or has expired", async () => {
    linkPreview.mockRejectedValue(new admin.SignInRefused("sign_in_expired"));
    render(<LinkConfirmation token="spent" />);
    expect(await screen.findByTestId("link-expired")).toBeInTheDocument();
  });

  it("says so when confirming is refused", async () => {
    linkPreview.mockResolvedValue(PREVIEW);
    linkConfirm.mockRejectedValue(new admin.SignInRefused("not_allowed"));
    render(<LinkConfirmation token="t" />);
    fireEvent.click(await screen.findByRole("button", { name: "Link it" }));
    expect(await screen.findByTestId("link-refused")).toBeInTheDocument();
  });

  it("links nothing for someone who says it was not them", async () => {
    linkPreview.mockResolvedValue(PREVIEW);
    render(<LinkConfirmation token="t" />);
    fireEvent.click(await screen.findByRole("button", { name: "Not me" }));
    await waitFor(() => expect(screen.queryByTestId("link-confirmation")).not.toBeInTheDocument());
    expect(linkConfirm).not.toHaveBeenCalled();
  });
});

describe("the sign-in page", () => {
  it("asks about the link it was opened with", async () => {
    linkPreview.mockResolvedValue(PREVIEW);
    renderPage(<Login />, { path: "/login", initialPath: "/login#link=from-the-mail" });
    expect(await screen.findByTestId("link-confirmation")).toBeInTheDocument();
    expect(linkPreview).toHaveBeenCalledWith("from-the-mail");
  });

  it("explains a sign-in that waits for the account's owner", async () => {
    renderPage(<Login />, { path: "/login", initialPath: "/login?error=link_confirmation_sent" });
    expect(await screen.findByText(/We sent a link to that address/)).toBeInTheDocument();
  });

  it("explains a sign-in that met an account nobody can confirm by mail", async () => {
    renderPage(<Login />, { path: "/login", initialPath: "/login?error=link_needs_confirmation" });
    expect(await screen.findByText(/An account with this address exists already\. Ask an administrator/)).toBeInTheDocument();
  });
});
