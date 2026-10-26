/** My Profile → Linked sign-ins: seeing, unlinking (never the last way in), linking another, and the answer. */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { adminAuthProviders, type AuthProviderPublic } from "~/api/admin";
import { ApiError } from "~/api/http";
import { signInsApi, type SignIns } from "~/api/signIns";
import { renderPage } from "~/test/renderPage";

import { LinkedSignInsCard } from "./LinkedSignInsCard";

const CORP: AuthProviderPublic = { key: "corp", name: "Corp", kind: "redirect" };
const AD: AuthProviderPublic = { key: "ad", name: "Corp AD", kind: "password" };
const LINK = {
  id: "l1", provider: "corp", provider_name: "Corp", account_email: "ada@corp.example",
  last_sign_in_at: "2026-10-26T09:00:00Z",
};

function given(signIns: SignIns, providers: AuthProviderPublic[] = [CORP, AD]) {
  vi.spyOn(signInsApi, "mine").mockResolvedValue(signIns);
  vi.spyOn(adminAuthProviders, "listPublic").mockResolvedValue(providers);
}

const open = (at = "/admin/profile") => renderPage(<LinkedSignInsCard />, { path: "/admin/profile", initialPath: at });

beforeEach(() => {
  window.location.hash = "";
});

describe("linked sign-ins on My Profile", () => {
  it("is not shown without providers or links", async () => {
    given({ links: [], password: true }, []);
    renderPage(<LinkedSignInsCard />);
    await waitFor(() => expect(signInsApi.mine).toHaveBeenCalled());
    expect(screen.queryByTestId("linked-sign-ins-card")).not.toBeInTheDocument();
  });

  it("never offers to unlink the last way in", async () => {
    given({ links: [LINK], password: false });
    open();
    const unlink = await screen.findByRole("button", { name: "Unlink" });
    expect(unlink).toBeDisabled();
    expect(screen.getByText(/Your password does not sign you in/)).toBeInTheDocument();
  });

  it("unlinks after asking, while the password still signs in", async () => {
    given({ links: [LINK], password: true });
    const unlink = vi.spyOn(signInsApi, "unlinkMine").mockResolvedValue(undefined);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Unlink" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Unlink Corp? You can no longer sign in with it");
    fireEvent.click(within(dialog).getByRole("button", { name: "Unlink" }));
    await waitFor(() => expect(unlink).toHaveBeenCalledWith("l1"));
  });

  it("links a provider people are sent to: off to it, to come back here", async () => {
    given({ links: [], password: true });
    const start = vi.spyOn(signInsApi, "startLink").mockResolvedValue({ redirect_url: "#idp" });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Link a provider" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Corp" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("corp"));
    await waitFor(() => expect(window.location.hash).toBe("#idp"));
  });

  it("links a directory with its account name and password, and says why not", async () => {
    given({ links: [LINK], password: true });
    const link = vi.spyOn(signInsApi, "linkDirectory").mockRejectedValueOnce(new ApiError("wrong_credentials", 400));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Link a provider" }));
    expect(screen.queryByRole("menuitem", { name: "Corp" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Corp AD" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Account name"), { target: { value: "ada" } });
    fireEvent.change(within(dialog).getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Link" }));
    expect(await within(dialog).findByText("The account name or password is wrong.")).toBeInTheDocument();
    link.mockResolvedValueOnce(undefined);
    fireEvent.click(within(dialog).getByRole("button", { name: "Link" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(link).toHaveBeenLastCalledWith("ad", "ada", "secret");
  });

  it("says what came back from the provider", async () => {
    given({ links: [LINK], password: true });
    open("/admin/profile?linked=corp");
    expect(await screen.findByText("Corp is linked: you can sign in with it now.")).toBeInTheDocument();
  });

  it("puts no words of a crafted address on the page", async () => {
    given({ links: [LINK], password: true });
    open("/admin/profile?linked=Call%20%2B1%20555%200100&link_refused=Call%20us");
    expect(await screen.findByTestId("linked-sign-ins")).toBeInTheDocument();
    expect(screen.queryByText(/Call/)).not.toBeInTheDocument();
    expect(screen.getByText("The sign-in did not work.")).toBeInTheDocument();
  });

  it("says why a link was refused, in the person's words", async () => {
    given({ links: [], password: true });
    open("/admin/profile?link_refused=already_linked");
    expect(await screen.findByText(/This sign-in belongs to another account here/)).toBeInTheDocument();
  });
});
