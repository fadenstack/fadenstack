/** The provider form's rules (model.ts): defaults, addresses, what is sent, and what is still missing. */
import { describe, expect, it } from "vitest";

import type { SignInProvider } from "~/api/signInProviders";

import {
  addresses, blankForm, formFromProvider, keyFromName, plainLdap, plainServerAddress, problems, toWrite,
} from "./model";

const ORIGIN = "https://ai.example.internal";

function oidcForm() {
  return {
    ...blankForm("oidc", ORIGIN),
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
    const write = toWrite(oidcForm());
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
    const form = { ...blankForm("saml", ORIGIN), key: "idp", name: "IdP", idpMetadataUrl: "https://idp.example.com/md" };
    expect(toWrite(form).settings).toMatchObject({
      protocol: "saml", idp_metadata_url: "https://idp.example.com/md", idp_metadata_xml: null,
      sp_entity_id: `${ORIGIN}/api/auth/sso/idp/metadata`, acs_url: `${ORIGIN}/api/auth/sso/idp/acs`,
    });
    const pasted = toWrite({ ...form, metadataSource: "xml", idpMetadataXml: " <EntityDescriptor/> " });
    expect(pasted.settings).toMatchObject({ idp_metadata_url: null, idp_metadata_xml: "<EntityDescriptor/>" });
  });

  it("a directory: empty optional fields as null, the service account's password", () => {
    const form = {
      ...blankForm("ldap"), key: "ad", name: "AD", ldapUrl: "ldaps://dc1.example.com", bindDn: "svc@example.com",
      bindPassword: "pw", baseDn: "DC=example,DC=com",
    };
    const write = toWrite(form);
    expect(write.settings).toMatchObject({ protocol: "ldap", ca_bundle: null, user_filter: null, preset: "ad" });
    expect(write.secrets).toEqual({ bind_password: "pw" });
  });

  it("the policy: domains split and cleaned, hours in seconds, empty lifetimes as none, half rules left out", () => {
    const form = {
      ...oidcForm(), allowedDomains: "@example.com, example.de;  ", sessionHours: "1.5", inactiveDays: "",
      groupRules: [{ external: "AI Users", group_id: "g1" }, { external: " ", group_id: "g2" }, { external: "x", group_id: "" }],
    };
    expect(toWrite(form).policy).toMatchObject({
      allowed_domains: ["example.com", "example.de"], session_lifetime_s: 5400, inactive_days: null,
      group_rules: [{ external: "AI Users", group_id: "g1" }],
    });
  });
});

describe("changing a provider", () => {
  it("reads the stored provider into the form, never a secret", () => {
    const form = formFromProvider(STORED);
    expect(form).toMatchObject({
      key: "corp", serverAddress: "https://old.example.internal", issuer: "https://login.example.com/v2.0",
      clientId: "app-1", clientSecret: "",
      scopes: "openid email", userinfo: "always", allowedDomains: "example.com, example.de", sessionHours: "8",
      inactiveDays: "", ssoOnly: true, manageGroups: true, createAccount: false,
    });
  });

  it("keeps what the form does not show, sends no key, and no secret unless one is typed", () => {
    const form = { ...formFromProvider(STORED), name: "Renamed" };
    const write = toWrite(form, STORED);
    expect(write.key).toBeUndefined();
    expect(write.secrets).toBeUndefined();
    expect(write.settings).toMatchObject({
      redirect_uri: STORED.settings.protocol === "oidc" ? STORED.settings.redirect_uri : "",
      token_auth_method: "client_secret_post", email_claim: "upn", groups_claim: "roles",
    });
    expect(toWrite({ ...form, clientSecret: "new" }, STORED).secrets).toEqual({ client_secret: "new" });
  });
});

describe("what is still missing", () => {
  it("names each required field per protocol", () => {
    expect(problems(blankForm("oidc"), false)).toEqual([
      "name", "key", "server_address", "issuer", "client_id", "client_secret",
    ]);
    expect(problems(blankForm("saml", ORIGIN), false)).toEqual(["name", "key", "metadata"]);
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

describe("the server address", () => {
  it("is where the console is open, unless the admin changes it", () => {
    expect(blankForm("oidc", ORIGIN).serverAddress).toBe(ORIGIN);
    const moved = { ...oidcForm(), serverAddress: "https://ai.example.com/" };
    expect(toWrite(moved).settings).toMatchObject({ redirect_uri: "https://ai.example.com/api/auth/sso/corp/callback" });
  });

  it("can be changed on a stored provider, keeping everything else", () => {
    const form = { ...formFromProvider(STORED), serverAddress: "https://ai.example.com" };
    expect(toWrite(form, STORED).settings).toMatchObject({
      redirect_uri: "https://ai.example.com/api/auth/sso/corp/callback", email_claim: "upn",
    });
  });

  it("is a scheme and a host, nothing after them", () => {
    for (const serverAddress of ["https://ai.example.internal", "https://ai.example.internal/", "http://192.0.2.10:8080"]) {
      expect(problems({ ...oidcForm(), serverAddress }, false)).toEqual([]);
    }
    for (const serverAddress of ["ai.example.internal", "https://ai.example.internal/console", "https://a b", "ftp://x"]) {
      expect(problems({ ...oidcForm(), serverAddress }, false)).toContain("server_address");
    }
    expect(problems({ ...blankForm("ldap"), serverAddress: "" }, false)).not.toContain("server_address");
  });

  it("warns of a server address without HTTPS", () => {
    expect(plainServerAddress({ ...oidcForm(), serverAddress: "http://ai.example.internal" })).toBe(true);
    expect(plainServerAddress(oidcForm())).toBe(false);
    expect(plainServerAddress({ ...blankForm("ldap"), serverAddress: "http://x" })).toBe(false);
  });
});

describe("the SAML entity ID", () => {
  const saml = { ...blankForm("saml", ORIGIN), key: "idp", name: "IdP", idpMetadataUrl: "https://idp.example.com/md" };

  it("is the metadata address unless one is given", () => {
    expect(toWrite(saml).settings).toMatchObject({ sp_entity_id: `${ORIGIN}/api/auth/sso/idp/metadata` });
    expect(toWrite({ ...saml, spEntityId: " urn:example:fadenstack " }).settings).toMatchObject({
      sp_entity_id: "urn:example:fadenstack",
    });
  });

  it("follows a changed server address while it is the default, and stays when it was given", () => {
    const stored: SignInProvider = {
      ...STORED, key: "idp", protocol: "saml",
      settings: {
        protocol: "saml", idp_metadata_url: "https://idp.example.com/md", idp_metadata_xml: null, idp_entity_id: null,
        sp_entity_id: `${ORIGIN}/api/auth/sso/idp/metadata`, acs_url: `${ORIGIN}/api/auth/sso/idp/acs`,
        subject_attribute: null, email_verified_by_idp: false, sign_requests: false, want_response_signed: false,
        sp_certificate: "-----BEGIN CERTIFICATE-----",
      },
    };
    const form = formFromProvider(stored);
    expect([form.serverAddress, form.spEntityId]).toEqual([ORIGIN, ""]);
    expect(toWrite({ ...form, serverAddress: "https://ai.example.com" }, stored).settings).toMatchObject({
      sp_entity_id: "https://ai.example.com/api/auth/sso/idp/metadata",
      acs_url: "https://ai.example.com/api/auth/sso/idp/acs",
      sp_certificate: "-----BEGIN CERTIFICATE-----",
    });
    const given = formFromProvider({ ...stored, settings: { ...stored.settings, sp_entity_id: "urn:example:old-sp" } as never });
    expect(given.spEntityId).toBe("urn:example:old-sp");
  });

  it("is an absolute URI of at most 256 characters", () => {
    expect(problems({ ...saml, spEntityId: "not a uri" }, false)).toContain("entity_id");
    expect(problems({ ...saml, spEntityId: `urn:${"x".repeat(260)}` }, false)).toContain("entity_id");
    expect(problems({ ...saml, spEntityId: "https://sp.example.com/saml" }, false)).toEqual([]);
  });
});
