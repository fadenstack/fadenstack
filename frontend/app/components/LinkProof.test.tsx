/**
 * A sign-in that met an existing account waits for the proof that the account is the person's (backend
 * services/sign_in/link_requests.py): its password, or a code sent to its address while mail is on.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import * as admin from "~/api/admin";
import { auth } from "~/api/auth";
import { renderPage } from "~/test/renderPage";
import Login from "~/routes/login";

import { LinkProof } from "./LinkProof";

// Made before each test: the config restores spies between tests (vitest.config.ts).
let linkWaiting: MockInstance<typeof admin.adminAuthProviders.linkWaiting>;
let linkWithPassword: MockInstance<typeof admin.adminAuthProviders.linkWithPassword>;
let linkWithCode: MockInstance<typeof admin.adminAuthProviders.linkWithCode>;
let linkSendCode: MockInstance<typeof admin.adminAuthProviders.linkSendCode>;
const WAITING: admin.LinkWaiting = {
  provider_name: "Corp", account_email: "ada@example.com", expires_at: "2026-10-25T13:00:00Z",
  mail: false, code_sent: false,
};

beforeEach(() => {
  linkWaiting = vi.spyOn(admin.adminAuthProviders, "linkWaiting");
  linkWithPassword = vi.spyOn(admin.adminAuthProviders, "linkWithPassword");
  linkWithCode = vi.spyOn(admin.adminAuthProviders, "linkWithCode");
  linkSendCode = vi.spyOn(admin.adminAuthProviders, "linkSendCode");
});

function shown(props: Partial<{ onSignedIn: () => void; onCancel: () => void }> = {}) {
  const onSignedIn = props.onSignedIn ?? vi.fn();
  const onCancel = props.onCancel ?? vi.fn();
  render(<LinkProof onSignedIn={onSignedIn} onCancel={onCancel} />);
  return { onSignedIn, onCancel };
}

describe("proving the account is yours", () => {
  it("with its password: linked, and signed in", async () => {
    linkWaiting.mockResolvedValue(WAITING);
    linkWithPassword.mockResolvedValue();
    const { onSignedIn } = shown();
    const proof = await screen.findByTestId("link-proof");
    expect(proof).toHaveTextContent("An account with the address ada@example.com exists here already");
    expect(proof).toHaveTextContent("Link your Corp sign-in");
    fireEvent.change(screen.getByLabelText(/^Password of ada@example\.com/), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Link and sign in" }));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect(linkWithPassword).toHaveBeenCalledWith("secret");
  });

  it("a wrong password reads as such and signs nobody in", async () => {
    linkWaiting.mockResolvedValue(WAITING);
    linkWithPassword.mockRejectedValue(new admin.SignInRefused("wrong_credentials"));
    const { onSignedIn } = shown();
    fireEvent.change(await screen.findByLabelText(/^Password of/), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Link and sign in" }));
    expect(await screen.findByText("The password or code is wrong.")).toBeInTheDocument();
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it("without mail: no code, an administrator instead", async () => {
    linkWaiting.mockResolvedValue(WAITING);
    shown();
    expect(await screen.findByText(/An administrator can set a new one for it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Get a code by e-mail/ })).not.toBeInTheDocument();
  });

  it("with a code sent to the account's address", async () => {
    linkWaiting.mockResolvedValue({ ...WAITING, mail: true });
    linkSendCode.mockResolvedValue();
    linkWithCode.mockResolvedValue();
    const { onSignedIn } = shown();
    fireEvent.click(await screen.findByRole("button", { name: /Get a code by e-mail/ }));
    expect(await screen.findByTestId("link-code-sent")).toHaveTextContent("We sent a code to ada@example.com");
    expect(linkSendCode).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText(/^Code from the e-mail/), { target: { value: "1234 5678" } });
    fireEvent.click(screen.getByRole("button", { name: "Link and sign in" }));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect(linkWithCode).toHaveBeenCalledWith("1234 5678");
  });

  it("says when another code has to wait", async () => {
    linkWaiting.mockResolvedValue({ ...WAITING, mail: true, code_sent: true });
    linkSendCode.mockRejectedValue(new admin.SignInRefused("too_soon"));
    shown();
    fireEvent.click(await screen.findByRole("button", { name: "Send a new code" }));
    expect(await screen.findByText("Wait a minute before asking for another code.")).toBeInTheDocument();
  });

  it("says so when the sign-in no longer waits, and goes back", async () => {
    linkWaiting.mockRejectedValue(new admin.SignInRefused("sign_in_expired"));
    const { onCancel } = shown();
    expect(await screen.findByTestId("link-expired")).toHaveTextContent("Sign in with your provider again");
    fireEvent.click(screen.getByRole("button", { name: "Back to sign-in" }));
    expect(onCancel).toHaveBeenCalled();
  });
});

describe("the sign-in page", () => {
  beforeEach(() => {
    vi.spyOn(admin.adminAuthProviders, "listPublic").mockResolvedValue([]);
    vi.spyOn(auth, "me").mockRejectedValue(new Error("not signed in"));
  });

  it("asks for the proof when a provider's sign-in met an existing account", async () => {
    linkWaiting.mockResolvedValue(WAITING);
    renderPage(<Login />, { path: "/login", initialPath: "/login?error=link_needs_confirmation" });
    expect(await screen.findByTestId("link-proof")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign In" })).not.toBeInTheDocument();
  });

  it("offers a reset by mail only while mail can be sent", async () => {
    vi.spyOn(auth, "options").mockResolvedValue({ password_reset: true });
    renderPage(<Login />, { path: "/login" });
    expect(await screen.findByRole("link", { name: "Forgot password?" })).toBeInTheDocument();
  });

  it("sends people to an administrator without mail", async () => {
    vi.spyOn(auth, "options").mockResolvedValue({ password_reset: false });
    renderPage(<Login />, { path: "/login" });
    expect(await screen.findByTestId("ask-admin")).toHaveTextContent("An administrator can set a new one.");
    expect(screen.queryByRole("link", { name: "Forgot password?" })).not.toBeInTheDocument();
  });
});
