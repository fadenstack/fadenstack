/** The provider form's rules (model.ts): defaults, addresses, what is sent, and what is still missing. */
import { describe, expect, it } from "vitest";

import type { SignInProvider } from "~/api/signInProviders";

import { addresses, blankForm, formFromProvider, keyFromName, plainLdap, problems, toWrite } from "./model";

const ORIGIN = "https://ai.example.internal";

function oidcForm() {
  return {
    ...blankForm("oidc"),
    key: "corp", name: "Example Corp", issuer: "https://login.example.com/v2.0 ", clientId: " app-1 ",
    clientSecret: "s3cret",
  };
}

const STORED: SignInProvider = {
  id: "p1", key: "corp", name: "Example Corp", protocol: "oidc", enabled: true,
  settings: {
    protocol: "oidc", issuer: "https://login.example.com/v2.0", client_id: "app-1",
    redirect_uri: "https://old.example.internal/api/auth/sso/corp/callback", scopes: ["openid", "email"],
    token_auth_method: "client_secret_post", userinfo: "always", email_claim: "upn", email_verified_claim: "email_verified",
    name_claim: "name", groups_claim: "roles", max_id_token_age_s: 600, ca_bundle: null,
  },
  policy: {
    create_account: false, default_role_ids: ["r1"], allowed_domains: ["example.com", "example.de"],
    manage_groups: true, group_rules: [{ external: "AI Users", group_id: "g1" }], session_lifetime_s: 28_800,
    inactive_days: null, sso_only: true,
  },
  secrets_set: ["client_secret"], created_at: "", updated_at: "",
};

describe("defaults", () => {
  it("are the owner's: 12-hour sign-ins, suspension after 45 days, accounts created", () => {
    const form = blankForm("ldap");
    expect([form.sessionHours, form.inactiveDays, form.createAccount, form.preset]).toEqual(["12", "45", true, "ad"]);
    expect(blankForm().scopes).toBe("openid email profile");
  });

  it("make a key from the name: lower case, letters, digits and dashes", () => {
    expect(keyFromName("Müller & Söhne GmbH")).toBe("muller-sohne-gmbh");
    expect(keyFromName("  --Example Corp--  ")).toBe("example-corp");
    expect(keyFromName("x".repeat(70))).toHaveLength(64);
  });

  it("name the addresses the provider is registered with", () => {
    expect(addresses("corp", ORIGIN)).toEqual({
      callback: `${ORIGIN}/api/auth/sso/corp/callback`,
      acs: `${ORIGIN}/api/auth/sso/corp/acs`,
      metadata: `${ORIGIN}/api/auth/sso/corp/metadata`,
    });
  });
});

describe("what a new provider sends", () => {
  it("an OIDC provider: trimmed fields, this server's callback, the secret", () => {
    const write = toWrite(oidcForm(), ORIGIN);
    expect(write).toMatchObject({
      key: "corp", name: "Example Corp", enabled: true,
      settings: {
        protocol: "oidc", issuer: "https://login.example.com/v2.0", client_id: "app-1",
        redirect_uri: `${ORIGIN}/api/auth/sso/corp/callback`, scopes: ["openid", "email", "profile"],
      },
      secrets: { client_secret: "s3cret" },
    });
  });

  it("a SAML provider: its metadata one way only, this server's entity ID and ACS", () => {
    const form = { ...blankForm("saml"), key: "idp", name: "IdP", idpMetadataUrl: "https://idp.example.com/md" };
    expect(toWrite(form, ORIGIN).settings).toMatchObject({
      protocol: "saml", idp_metadata_url: "https://idp.example.com/md", idp_metadata_xml: null,
      sp_entity_id: `${ORIGIN}/api/auth/sso/idp/metadata`, acs_url: `${ORIGIN}/api/auth/sso/idp/acs`,
    });
    const pasted = toWrite({ ...form, metadataSource: "xml", idpMetadataXml: " <EntityDescriptor/> " }, ORIGIN);
    expect(pasted.settings).toMatchObject({ idp_metadata_url: null, idp_metadata_xml: "<EntityDescriptor/>" });
  });

  it("a directory: empty optional fields as null, the service account's password", () => {
    const form = {
      ...blankForm("ldap"), key: "ad", name: "AD", ldapUrl: "ldaps://dc1.example.com", bindDn: "svc@example.com",
      bindPassword: "pw", baseDn: "DC=example,DC=com",
    };
    const write = toWrite(form, ORIGIN);
    expect(write.settings).toMatchObject({ protocol: "ldap", ca_bundle: null, user_filter: null, preset: "ad" });
    expect(write.secrets).toEqual({ bind_password: "pw" });
  });

  it("the policy: domains split and cleaned, hours in seconds, empty lifetimes as none, half rules left out", () => {
    const form = {
      ...oidcForm(), allowedDomains: "@example.com, example.de;  ", sessionHours: "1.5", inactiveDays: "",
      groupRules: [{ external: "AI Users", group_id: "g1" }, { external: " ", group_id: "g2" }, { external: "x", group_id: "" }],
    };
    expect(toWrite(form, ORIGIN).policy).toMatchObject({
      allowed_domains: ["example.com", "example.de"], session_lifetime_s: 5400, inactive_days: null,
      group_rules: [{ external: "AI Users", group_id: "g1" }],
    });
  });
});

describe("changing a provider", () => {
  it("reads the stored provider into the form, never a secret", () => {
    const form = formFromProvider(STORED);
    expect(form).toMatchObject({
      key: "corp", issuer: "https://login.example.com/v2.0", clientId: "app-1", clientSecret: "",
      scopes: "openid email", userinfo: "always", allowedDomains: "example.com, example.de", sessionHours: "8",
      inactiveDays: "", ssoOnly: true, manageGroups: true, createAccount: false,
    });
  });

  it("keeps what the form does not show, sends no key, and no secret unless one is typed", () => {
    const form = { ...formFromProvider(STORED), name: "Renamed" };
    const write = toWrite(form, ORIGIN, STORED);
    expect(write.key).toBeUndefined();
    expect(write.secrets).toBeUndefined();
    expect(write.settings).toMatchObject({
      redirect_uri: STORED.settings.protocol === "oidc" ? STORED.settings.redirect_uri : "",
      token_auth_method: "client_secret_post", email_claim: "upn", groups_claim: "roles",
    });
    expect(toWrite({ ...form, clientSecret: "new" }, ORIGIN, STORED).secrets).toEqual({ client_secret: "new" });
  });
});

describe("what is still missing", () => {
  it("names each required field per protocol", () => {
    expect(problems(blankForm("oidc"), false)).toEqual(["name", "key", "issuer", "client_id", "client_secret"]);
    expect(problems(blankForm("saml"), false)).toEqual(["name", "key", "metadata"]);
    expect(problems(blankForm("ldap"), false)).toEqual(["name", "key", "ldap_url", "bind_dn", "base_dn", "bind_password"]);
    expect(problems(oidcForm(), false)).toEqual([]);
  });

  it("a stored secret need not be typed again, and the key is not checked once made", () => {
    expect(problems({ ...oidcForm(), clientSecret: "", key: "Not A Key" }, true)).toEqual([]);
  });

  it("refuses a key the addresses cannot carry", () => {
    for (const key of ["-corp", "corp-", "Corp", "co rp", "x".repeat(65)]) {
      expect(problems({ ...oidcForm(), key }, false)).toContain("key");
    }
  });

  it("warns of a directory reached without TLS", () => {
    const ldap = { ...blankForm("ldap"), ldapUrl: "ldap://dc1.example.com" };
    expect(plainLdap(ldap)).toBe(true);
    expect(plainLdap({ ...ldap, startTls: true })).toBe(false);
    expect(plainLdap({ ...ldap, ldapUrl: "ldaps://dc1.example.com" })).toBe(false);
  });
});
