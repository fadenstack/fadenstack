/** The sign-in page with a directory (a ``password`` provider) and with refusals in the address. */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import * as admin from "~/api/admin";
import { auth, LoginRefused } from "~/api/auth";
import { renderPage } from "~/test/renderPage";

import Login from "./login";

let passwordSignIn: MockInstance<typeof admin.adminAuthProviders.passwordSignIn>;
let localLogin: MockInstance<typeof auth.login>;

beforeEach(() => {
  vi.spyOn(admin.adminAuthProviders, "listPublic").mockResolvedValue([
    { key: "corp-ad", name: "Corp AD", kind: "password" },
    { key: "entra", name: "Entra", kind: "redirect" },
  ]);
  vi.spyOn(auth, "me").mockRejectedValue(new Error("not signed in"));
  passwordSignIn = vi.spyOn(admin.adminAuthProviders, "passwordSignIn");
  localLogin = vi.spyOn(auth, "login");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
});

function signIn(name: string, password: string) {
  fireEvent.change(screen.getByLabelText(/^Account name/), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign In" }));
}

describe("signing in with a directory account", () => {
  it("offers the directory beside the Fadenstack account, and the redirect providers below", async () => {
    renderPage(<Login />, { path: "/login" });
    expect(await screen.findByRole("button", { name: "Corp AD" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fadenstack account" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Sign in with Entra" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in with Corp AD" })).not.toBeInTheDocument();
  });

  it("sends the name and password to the directory, not to the local sign-in", async () => {
    passwordSignIn.mockResolvedValue();
    const fresh = vi.spyOn(auth, "meFresh").mockResolvedValue({ is_superuser: false } as never);
    renderPage(<Login />, { path: "/login" });
    fireEvent.click(await screen.findByRole("button", { name: "Corp AD" }));
    expect(screen.queryByRole("link", { name: "Forgot password?" })).not.toBeInTheDocument();
    signIn("alex", "directory-password");
    await waitFor(() => expect(passwordSignIn).toHaveBeenCalledWith("corp-ad", "alex", "directory-password"));
    await waitFor(() => expect(fresh).toHaveBeenCalled());
    expect(localLogin).not.toHaveBeenCalled();
  });

  it("says why a directory sign-in was refused", async () => {
    passwordSignIn.mockRejectedValue(new admin.SignInRefused("wrong_credentials"));
    renderPage(<Login />, { path: "/login" });
    fireEvent.click(await screen.findByRole("button", { name: "Corp AD" }));
    signIn("alex", "wrong");
    expect(await screen.findByText("The account name or password is wrong.")).toBeInTheDocument();
  });

  it("asks for the proof when a directory sign-in meets an existing account", async () => {
    passwordSignIn.mockRejectedValue(new admin.SignInRefused("link_needs_confirmation"));
    vi.spyOn(admin.adminAuthProviders, "linkWaiting").mockResolvedValue({
      provider_name: "Corp AD", account_email: "alex@corp.example", expires_at: "2026-10-25T13:00:00Z",
      mail: false, code_sent: false,
    });
    renderPage(<Login />, { path: "/login" });
    fireEvent.click(await screen.findByRole("button", { name: "Corp AD" }));
    signIn("alex", "pw");
    expect(await screen.findByTestId("link-proof")).toHaveTextContent("alex@corp.example");
  });
});

describe("refusals in the address", () => {
  it("words the backend's own codes", async () => {
    renderPage(<Login />, { path: "/login", initialPath: "/login?error=email_not_trusted" });
    expect(await screen.findByText(/did not confirm your e-mail address/)).toBeInTheDocument();
  });

  it("shows no words a crafted link puts in it", async () => {
    renderPage(<Login />, { path: "/login", initialPath: "/login?error=Call%20%2B1%20555%200100%20now" });
    expect(await screen.findByText("The sign-in did not work.")).toBeInTheDocument();
    expect(screen.queryByText(/555 0100/)).not.toBeInTheDocument();
  });
});

describe("a refused password", () => {
  it.each([
    ["LOGIN_BAD_CREDENTIALS", "The e-mail or password is wrong."],
    ["LOGIN_USE_SIGN_IN_PROVIDER", "Your organisation signs you in with its own sign-in: use its button below."],
    ["TOO_MANY", "Too many attempts. Wait a minute and try again."],
    ["SOMETHING_NEW", "Sign-in failed."],
  ])("reads %s in words, never as the server's code", async (code, text) => {
    localLogin.mockRejectedValue(new LoginRefused(code));
    renderPage(<Login />, { path: "/login" });
    await screen.findByRole("button", { name: "Corp AD" });
    fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: "alex@example.com" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(code))).not.toBeInTheDocument();
  });
});
