/**
 * Outside sign-in providers, as the sign-in module keeps them (backend: `/api/admin/auth-providers/...`,
 * passed on to the module unchanged). The module checks every setting; its refusals come back here.
 */

export type Protocol = "oidc" | "saml" | "ldap";

export interface GroupRule {
  /** The group as the provider names it: its name, or (LDAP, SAML) its DN. */
  external: string;
  group_id: string;
}

export interface ProviderPolicy {
  create_account: boolean;
  default_role_ids: string[];
  allowed_domains: string[];
  manage_groups: boolean;
  group_rules: GroupRule[];
  session_lifetime_s: number | null;
  inactive_days: number | null;
  sso_only: boolean;
  /** Only people the provider's provisioning (SCIM) made sign in with it; superusers excepted. */
  provisioned_only?: boolean;
}

export interface OIDCSettings {
  protocol: "oidc";
  issuer: string;
  client_id: string;
  redirect_uri: string;
  scopes: string[];
  token_auth_method: "client_secret_basic" | "client_secret_post" | "none";
  userinfo: "never" | "when_missing" | "always";
  /** The claim that names the person: `sub`, or one the provider keeps across its apps (Entra ID's `oid`). */
  subject_claim?: string;
  email_claim: string;
  email_verified_claim: string;
  name_claim: string;
  groups_claim: string;
  max_id_token_age_s: number;
  ca_bundle: string | null;
}

export interface SAMLSettings {
  protocol: "saml";
  idp_metadata_xml: string | null;
  idp_metadata_url: string | null;
  idp_entity_id: string | null;
  sp_entity_id: string;
  acs_url: string;
  subject_attribute: string | null;
  email_verified_by_idp: boolean;
  sign_requests: boolean;
  want_response_signed: boolean;
  sp_certificate: string | null;
}

export interface LDAPSettings {
  protocol: "ldap";
  url: string;
  start_tls: boolean;
  ca_bundle: string | null;
  bind_dn: string;
  base_dn: string;
  preset: "ad" | "generic";
  user_filter: string | null;
  subject_attribute: string | null;
  email_attribute: string | null;
  name_attribute: string | null;
  group_filter: string | null;
  group_base_dn: string | null;
  disabled_filter: string | null;
  email_verified_by_idp: boolean;
  timeout_s: number;
  check_people: boolean;
}

export type ProviderSettings = OIDCSettings | SAMLSettings | LDAPSettings;

export interface ProviderSecrets {
  client_secret?: string;
  bind_password?: string;
}

export interface SignInProvider {
  id: string;
  key: string;
  name: string;
  protocol: Protocol;
  enabled: boolean;
  settings: ProviderSettings;
  policy: ProviderPolicy;
  /** Which secrets are set; never the secrets themselves. */
  secrets_set: string[];
  created_at: string;
  updated_at: string;
}

export interface ProviderWrite {
  key?: string;
  name: string;
  enabled: boolean;
  settings: Partial<ProviderSettings> & { protocol: Protocol };
  secrets?: ProviderSecrets;
  policy: ProviderPolicy;
}

/** A refused request: `status`, and the module's `detail` (a text, or validation errors). */
export class ProviderApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `status ${status}`);
  }

  /** No sign-in module is switched on (the backend answers 404 for every route). */
  get moduleOff(): boolean {
    return this.status === 404 && this.detail === "No sign-in module is switched on";
  }

  /** The validation messages, one per field, as the module gave them. */
  get messages(): string[] {
    if (Array.isArray(this.detail)) {
      return this.detail.map((item: { loc?: unknown[]; msg?: string }) => {
        const where = (item.loc ?? []).filter((part) => part !== "body").join(".");
        return where ? `${where}: ${item.msg ?? ""}` : (item.msg ?? "");
      });
    }
    return typeof this.detail === "string" ? [this.detail] : [];
  }
}

const BASE = "/api/admin/auth-providers";

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new ProviderApiError(res.status, body.detail ?? res.statusText);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const signInProviders = {
  list(): Promise<SignInProvider[]> {
    return call<SignInProvider[]>("/");
  },
  create(body: ProviderWrite): Promise<SignInProvider> {
    return call<SignInProvider>("/", { method: "POST", body: JSON.stringify(body) });
  },
  update(key: string, body: Omit<ProviderWrite, "key">): Promise<SignInProvider> {
    return call<SignInProvider>(`/${encodeURIComponent(key)}`, { method: "PATCH", body: JSON.stringify(body) });
  },
  remove(key: string): Promise<void> {
    return call<void>(`/${encodeURIComponent(key)}`, { method: "DELETE" });
  },
  /** Where the identity provider fetches this service provider's SAML metadata. */
  metadataUrl(key: string, origin: string): string {
    return `${origin}/api/auth/sso/${encodeURIComponent(key)}/metadata`;
  },
};
