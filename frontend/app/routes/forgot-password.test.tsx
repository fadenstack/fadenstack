/**
 * Asking for a reset mail: any address an account is made with goes through (``firma.local`` included), a
 * refusal is worded, and the page never shows the server's raw answer.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { auth, NotAnAddress } from "~/api/auth";
import { renderPage } from "~/test/renderPage";

import ForgotPassword from "./forgot-password";

let ask: MockInstance<typeof auth.forgotPassword>;

beforeEach(() => {
  vi.spyOn(auth, "options").mockResolvedValue({ password_reset: true });
  ask = vi.spyOn(auth, "forgotPassword");
});

async function asked(address: string) {
  renderPage(<ForgotPassword />, { path: "/forgot-password" });
  const field = await screen.findByLabelText(/^Email/);
  await waitFor(() => expect(field).not.toBeDisabled());
  fireEvent.change(field, { target: { value: address } });
  fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
}

describe("the forgotten password page", () => {
  it("sends an address at a special-use domain and says a link went out if the account exists", async () => {
    ask.mockResolvedValue();
    await asked("ada@firma.local");
    expect(await screen.findByText(/If an account exists for that email/)).toBeInTheDocument();
    expect(ask).toHaveBeenCalledWith("ada@firma.local");
  });

  it("words an address the server refuses (the field already stops what no browser takes)", async () => {
    ask.mockRejectedValue(new NotAnAddress());
    await asked("ada@firma.local");
    expect(await screen.findByText("Enter an email address, e.g. name@example.com.")).toBeInTheDocument();
  });

  it("never shows the server's raw answer", async () => {
    ask.mockRejectedValue(new Error('API 500: {"detail":"internal"}'));
    await asked("ada@firma.local");
    expect(await screen.findByText("Failed to request password reset.")).toBeInTheDocument();
    expect(screen.queryByText(/API 500/)).not.toBeInTheDocument();
  });
});

describe("auth.forgotPassword", () => {
  it("sends the address trimmed and turns a 422 into NotAnAddress", async () => {
    ask.mockRestore();
    const fetched = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 202 }))
      .mockResolvedValueOnce(new Response('{"detail":[]}', { status: 422 }));
    await auth.forgotPassword("  ada@firma.local ");
    expect(JSON.parse(String(fetched.mock.calls[0][1]?.body))).toEqual({ email: "ada@firma.local" });
    await expect(auth.forgotPassword("ada")).rejects.toBeInstanceOf(NotAnAddress);
    fetched.mockRestore();
  });
});
