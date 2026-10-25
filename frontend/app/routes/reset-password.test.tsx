/**
 * Setting a new password from a link: an administrator's (``#link=``) or a reset mail's (``#token=``, and
 * ``?token=`` in mails sent before). The token never stays in the address bar; nobody is signed in afterwards.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { auth, ResetRefused } from "~/api/auth";
import { renderPage } from "~/test/renderPage";

import ResetPassword from "./reset-password";

let preview: MockInstance<typeof auth.resetLinkPreview>;
let withLink: MockInstance<typeof auth.resetWithLink>;
let byMail: MockInstance<typeof auth.resetPassword>;
const NEW = "A-new-password-for-2026!";

beforeEach(() => {
  preview = vi.spyOn(auth, "resetLinkPreview");
  withLink = vi.spyOn(auth, "resetWithLink");
  byMail = vi.spyOn(auth, "resetPassword");
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

function opened(address: string) {
  window.history.replaceState(null, "", address);
  renderPage(<ResetPassword />, { path: "/reset-password" });
}

function choose(password: string) {
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: password } });
  fireEvent.change(screen.getByLabelText(/^Confirm password/), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Reset password" }));
}

describe("a link from an administrator", () => {
  it("says whose password it sets, sets it once, and keeps the token out of the address bar", async () => {
    preview.mockResolvedValue({ email: "alex@example.com", expires_at: "2026-10-25T21:00:00Z" });
    withLink.mockResolvedValue();
    opened("/reset-password#link=the-admins-token-0123456789");
    expect(await screen.findByTestId("reset-for")).toHaveTextContent("Choose a new password for alex@example.com");
    expect(preview).toHaveBeenCalledWith("the-admins-token-0123456789");
    expect(window.location.hash).toBe("");
    choose(NEW);
    await waitFor(() => expect(withLink).toHaveBeenCalledWith("the-admins-token-0123456789", NEW));
    expect(await screen.findByText(/Your password has been reset/)).toBeInTheDocument();
    expect(byMail).not.toHaveBeenCalled();
  });

  it("says so when the link no longer works", async () => {
    preview.mockResolvedValue(null);
    opened("/reset-password#link=spent-token-0123456789");
    expect(await screen.findByTestId("reset-expired")).toHaveTextContent("Ask your administrator for a new one");
    expect(screen.getByRole("button", { name: "Reset password" })).toBeDisabled();
  });

  it("shows the password rule's words and lets the person try again", async () => {
    preview.mockResolvedValue({ email: "alex@example.com", expires_at: "2026-10-25T21:00:00Z" });
    withLink.mockRejectedValueOnce(new ResetRefused("The password is too common."));
    opened("/reset-password#link=the-admins-token-0123456789");
    await screen.findByTestId("reset-for");
    choose("Password123456");
    expect(await screen.findByText("The password is too common.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset password" })).toBeEnabled();
  });
});

describe("a link from a reset mail", () => {
  it.each([
    ["in the fragment", "/reset-password#token=mailed-token"],
    ["in the query, as mails sent before", "/reset-password?token=mailed-token"],
  ])("takes the token %s", async (_how, address) => {
    byMail.mockResolvedValue();
    opened(address);
    await waitFor(() => expect(window.location.search + window.location.hash).toBe(""));
    choose(NEW);
    await waitFor(() => expect(byMail).toHaveBeenCalledWith("mailed-token", NEW));
    expect(preview).not.toHaveBeenCalled();
  });
});
