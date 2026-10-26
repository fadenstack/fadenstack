/**
 * Linked sign-ins: the outside providers an account signs in with, besides its password. One's own on My
 * Profile (`/api/admin/users/me/sign-ins`), anyone's for administrators (`/api/admin/users/{id}/sign-ins`).
 * Linking a provider while signed in starts at `/api/auth/sso/{key}/link-start` and comes back to My Profile
 * with `?linked=<key>` or `?link_refused=<code>`.
 *
 * Refusals carry a code as their message: `last_way` (the account would have no way in left),
 * `already_linked`, `not_provisioned`, and the sign-in page's own (`wrong_credentials`, ...).
 */
import { json, requester, segment } from "./http";

const users = requester("/api/admin/users");
const sso = requester("/api/auth/sso");

export interface LinkedSignIn {
  id: string;
  provider: string;
  /** The provider's name on the sign-in page; null once it is offered no more. */
  provider_name: string | null;
  /** The address the provider gave at the last sign-in. */
  account_email: string | null;
  last_sign_in_at: string | null;
}

/** Where My Profile can be open: the console's page, or the chat's dialog (people who only chat). */
export type ProfileAt = "/admin/profile" | "/chat";

export interface SignIns {
  links: LinkedSignIn[];
  /** The account's password is a way in: someone knows it, and no SSO-only domain refuses it. */
  password: boolean;
}

/** Whether *link* may be unlinked: never the last way in. */
export function canUnlink(signIns: SignIns): boolean {
  return signIns.password || signIns.links.length > 1;
}

export const signInsApi = {
  mine: () => users<SignIns>("/me/sign-ins"),
  unlinkMine: (linkId: string) => users<void>(`/me/sign-ins/${segment(linkId)}`, json("DELETE")),
  of: (userId: string) => users<SignIns>(`/${segment(userId)}/sign-ins`),
  unlink: (userId: string, linkId: string) =>
    users<void>(`/${segment(userId)}/sign-ins/${segment(linkId)}`, json("DELETE")),
  /**
   * Where to send the browser to sign in with *key*, to link it to the signed-in account; the answer comes back
   * to *back*, where My Profile is open (the console's page, or the chat's dialog).
   */
  startLink: (key: string, back: ProfileAt = "/admin/profile") =>
    sso<{ redirect_url: string }>(`/${segment(key)}/link-start`, json("POST", { back })),
  /** Link a directory account: its name and password prove it. */
  linkDirectory: (key: string, username: string, password: string) =>
    sso<void>(`/${segment(key)}/link-password`, json("POST", { username, password })),
};
