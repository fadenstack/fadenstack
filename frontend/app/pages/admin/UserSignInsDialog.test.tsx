/** Someone's linked sign-ins, for an administrator: seen, unlinked after asking, never the last way in. */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AdminUser } from "~/api/admin";
import { ApiError } from "~/api/http";
import { signInsApi } from "~/api/signIns";

import { UserSignInsDialog } from "./UserSignInsDialog";

const ADA = { id: "u1", email: "ada@corp.example" } as AdminUser;
const LINKS = [
  { id: "l1", provider: "corp", provider_name: "Corp", account_email: null, last_sign_in_at: null },
  { id: "l2", provider: "gone", provider_name: null, account_email: "ada@old.example", last_sign_in_at: null },
];

describe("an administrator's view of someone's linked sign-ins", () => {
  it("lists them, a provider offered no more by its key, and unlinks one after asking", async () => {
    vi.spyOn(signInsApi, "of").mockResolvedValue({ links: LINKS, password: false });
    const unlink = vi.spyOn(signInsApi, "unlink").mockResolvedValue(undefined);
    render(<UserSignInsDialog user={ADA} onClose={() => {}} />);
    expect(await screen.findByText("gone")).toBeInTheDocument();
    expect(screen.getByText(/Their password does not sign them in/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Unlink" })[1]);
    const confirm = screen.getAllByRole("dialog").at(-1)!;
    expect(confirm).toHaveTextContent("Unlink gone from ada@corp.example?");
    fireEvent.click(within(confirm).getByRole("button", { name: "Unlink" }));
    await waitFor(() => expect(unlink).toHaveBeenCalledWith("u1", "l2"));
  });

  it("says why the last way in stays", async () => {
    vi.spyOn(signInsApi, "of").mockResolvedValue({ links: LINKS, password: false });
    vi.spyOn(signInsApi, "unlink").mockRejectedValue(new ApiError("last_way", 409));
    render(<UserSignInsDialog user={ADA} onClose={() => {}} />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Unlink" }))[0]);
    fireEvent.click(within(screen.getAllByRole("dialog").at(-1)!).getByRole("button", { name: "Unlink" }));
    expect(await screen.findByText(/Give them a password reset link first/)).toBeInTheDocument();
  });
});
