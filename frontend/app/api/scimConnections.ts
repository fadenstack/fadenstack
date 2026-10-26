/**
 * Provisioning (`/api/ee/scim/connections`, for administrators): a sign-in provider's identity provider adds,
 * changes and suspends people and keeps teams here over SCIM 2.0, as they change there. One connection per
 * provider, with its own token. Offered while the server lists the feature {@link SCIM_PROVISIONING}
 * (`GET /api/about`). A token is shown once, when it is made; afterwards only its last four characters.
 */
import { json, requester, segment } from "./http";

const request = requester("/api/ee/scim/connections");

/** The feature the screens show for. */
export const SCIM_PROVISIONING = "scim_provisioning";

export interface ScimConnection {
  provider_key: string;
  /** Where the identity provider pushes (its "Tenant URL" or "SCIM connector base URL"). */
  base_url: string;
  /** The token's last four characters. */
  token_hint: string;
  created_at: string;
  token_made_at: string;
  last_push_at: string | null;
  /** The last refused push: its status and SCIM error type, never what was sent. */
  last_error: string | null;
  last_error_at: string | null;
}

export interface ScimToken {
  provider_key: string;
  base_url: string;
  /** Shown this once. */
  token: string;
}

export const scimConnectionsApi = {
  list: () => request<ScimConnection[]>(""),
  /** Switch provisioning on for the provider, or replace its token: the old one stops working at once. */
  makeToken: (provider: string) => request<ScimToken>(`/${segment(provider)}`, json("POST")),
  /** Switch provisioning off: the token stops working; the people and teams it made stay. */
  remove: (provider: string) => request<void>(`/${segment(provider)}`, json("DELETE")),
};
