/**
 * The provider form, and what it sends: defaults for each protocol, the addresses a provider is registered
 * with, and the payload. Pure functions, so the page stays thin and the rules are tested on their own.
 *
 * Editing merges into the stored settings: fields the form does not show (claims, attributes, CA bundles)
 * are kept, not reset.
 */
import type {
  GroupRule,
  LDAPSettings,
  OIDCSettings,
  Protocol,
  ProviderPolicy,
  ProviderSettings,
  ProviderWrite,
  SAMLSettings,
  SignInProvider,
} from "~/api/signInProviders";

/** A provider's key: what its addresses are made of (`/api/auth/sso/<key>/...`). */
export const KEY_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

export interface ProviderForm {
  key: string;
  name: string;
  enabled: boolean;
  protocol: Protocol;
  // OIDC
  issuer: string;
  clientId: string;
  clientSecret: string;
  scopes: string;
  userinfo: OIDCSettings["userinfo"];
  // SAML
  metadataSource: "url" | "xml";
  idpMetadataUrl: string;
  idpMetadataXml: string;
  emailVerifiedByIdp: boolean;
  signRequests: boolean;
  // LDAP
  ldapUrl: string;
  startTls: boolean;
  caBundle: string;
  bindDn: string;
  bindPassword: string;
  baseDn: string;
  preset: LDAPSettings["preset"];
  userFilter: string;
  checkPeople: boolean;
  // What happens to people who sign in
  createAccount: boolean;
  defaultRoleIds: string[];
  allowedDomains: string;
  manageGroups: boolean;
  groupRules: GroupRule[];
  sessionHours: string;
  inactiveDays: string;
  ssoOnly: boolean;
}

/** The addresses the provider is set up with: the callback (OIDC), the ACS and the metadata (SAML). */
export function addresses(key: string, origin: string) {
  const base = `${origin}/api/auth/sso/${key || "<key>"}`;
  return { callback: `${base}/callback`, acs: `${base}/acs`, metadata: `${base}/metadata` };
}

export function blankForm(protocol: Protocol = "oidc"): ProviderForm {
  return {
    key: "", name: "", enabled: true, protocol,
    issuer: "", clientId: "", clientSecret: "", scopes: "openid email profile", userinfo: "when_missing",
    metadataSource: "url", idpMetadataUrl: "", idpMetadataXml: "", emailVerifiedByIdp: false, signRequests: false,
    ldapUrl: "", startTls: false, caBundle: "", bindDn: "", bindPassword: "", baseDn: "", preset: "ad",
    userFilter: "", checkPeople: true,
    createAccount: true, defaultRoleIds: [], allowedDomains: "", manageGroups: false, groupRules: [],
    sessionHours: "12", inactiveDays: "45", ssoOnly: false,
  };
}

/** A key from the name, for a new provider: lower case, letters, digits and dashes. */
export function keyFromName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/, "");
}

export function formFromProvider(provider: SignInProvider): ProviderForm {
  const form = blankForm(provider.protocol);
  const policy = provider.policy;
  Object.assign(form, {
    key: provider.key, name: provider.name, enabled: provider.enabled,
    createAccount: policy.create_account, defaultRoleIds: policy.default_role_ids,
    allowedDomains: policy.allowed_domains.join(", "), manageGroups: policy.manage_groups,
    groupRules: policy.group_rules,
    sessionHours: policy.session_lifetime_s ? String(policy.session_lifetime_s / 3600) : "",
    inactiveDays: policy.inactive_days ? String(policy.inactive_days) : "", ssoOnly: policy.sso_only,
  });
  const s = provider.settings;
  if (s.protocol === "oidc") {
    Object.assign(form, { issuer: s.issuer, clientId: s.client_id, scopes: s.scopes.join(" "), userinfo: s.userinfo });
  } else if (s.protocol === "saml") {
    Object.assign(form, {
      metadataSource: s.idp_metadata_url ? "url" : "xml", idpMetadataUrl: s.idp_metadata_url ?? "",
      idpMetadataXml: s.idp_metadata_xml ?? "", emailVerifiedByIdp: s.email_verified_by_idp,
      signRequests: s.sign_requests,
    });
  } else {
    Object.assign(form, {
      ldapUrl: s.url, startTls: s.start_tls, caBundle: s.ca_bundle ?? "", bindDn: s.bind_dn, baseDn: s.base_dn,
      preset: s.preset, userFilter: s.user_filter ?? "", emailVerifiedByIdp: s.email_verified_by_idp,
      checkPeople: s.check_people,
    });
  }
  return form;
}

function policyOf(form: ProviderForm): ProviderPolicy {
  const hours = Number.parseFloat(form.sessionHours);
  const days = Number.parseInt(form.inactiveDays, 10);
  return {
    create_account: form.createAccount,
    default_role_ids: form.defaultRoleIds,
    allowed_domains: form.allowedDomains.split(/[\s,;]+/).map((d) => d.trim().replace(/^@/, "")).filter(Boolean),
    manage_groups: form.manageGroups,
    group_rules: form.groupRules.filter((rule) => rule.external.trim() && rule.group_id),
    session_lifetime_s: Number.isFinite(hours) && hours > 0 ? Math.round(hours * 3600) : null,
    inactive_days: Number.isFinite(days) && days > 0 ? days : null,
    sso_only: form.ssoOnly,
  };
}

function settingsOf(form: ProviderForm, origin: string, stored?: ProviderSettings): ProviderWrite["settings"] {
  const here = addresses(form.key, origin);
  if (form.protocol === "oidc") {
    const base = stored?.protocol === "oidc" ? stored : undefined;
    const settings: Partial<OIDCSettings> = {
      ...base, protocol: "oidc", issuer: form.issuer.trim(), client_id: form.clientId.trim(),
      redirect_uri: base?.redirect_uri ?? here.callback,
      scopes: form.scopes.split(/\s+/).filter(Boolean), userinfo: form.userinfo,
    };
    return settings as ProviderWrite["settings"];
  }
  if (form.protocol === "saml") {
    const base = stored?.protocol === "saml" ? stored : undefined;
    const byUrl = form.metadataSource === "url";
    const settings: Partial<SAMLSettings> = {
      ...base, protocol: "saml",
      idp_metadata_url: byUrl ? form.idpMetadataUrl.trim() : null,
      idp_metadata_xml: byUrl ? null : form.idpMetadataXml.trim(),
      sp_entity_id: base?.sp_entity_id ?? here.metadata, acs_url: base?.acs_url ?? here.acs,
      email_verified_by_idp: form.emailVerifiedByIdp, sign_requests: form.signRequests,
    };
    return settings as ProviderWrite["settings"];
  }
  const base = stored?.protocol === "ldap" ? stored : undefined;
  const settings: Partial<LDAPSettings> = {
    ...base, protocol: "ldap", url: form.ldapUrl.trim(), start_tls: form.startTls,
    ca_bundle: form.caBundle.trim() || null, bind_dn: form.bindDn.trim(), base_dn: form.baseDn.trim(),
    preset: form.preset, user_filter: form.userFilter.trim() || null,
    email_verified_by_idp: form.emailVerifiedByIdp, check_people: form.checkPeople,
  };
  return settings as ProviderWrite["settings"];
}

/** What the form sends: a new provider, or the changes to *stored*. Secrets only when typed. */
export function toWrite(form: ProviderForm, origin: string, stored?: SignInProvider): ProviderWrite {
  const secrets: ProviderWrite["secrets"] = {};
  if (form.protocol === "oidc" && form.clientSecret) secrets.client_secret = form.clientSecret;
  if (form.protocol === "ldap" && form.bindPassword) secrets.bind_password = form.bindPassword;
  const write: ProviderWrite = {
    name: form.name.trim(),
    enabled: form.enabled,
    settings: settingsOf(form, origin, stored?.settings),
    policy: policyOf(form),
  };
  if (!stored) write.key = form.key;
  if (Object.keys(secrets).length) write.secrets = secrets;
  return write;
}

/** What still keeps the form from being sent, as text keys (`auth_providers.problem.<key>`). */
export function problems(form: ProviderForm, editing: boolean): string[] {
  const found: string[] = [];
  if (!form.name.trim()) found.push("name");
  if (!editing && !KEY_PATTERN.test(form.key)) found.push("key");
  if (form.protocol === "oidc") {
    if (!/^https?:\/\//.test(form.issuer.trim())) found.push("issuer");
    if (!form.clientId.trim()) found.push("client_id");
    if (!editing && !form.clientSecret) found.push("client_secret");
  } else if (form.protocol === "saml") {
    if (form.metadataSource === "url" && !/^https?:\/\//.test(form.idpMetadataUrl.trim())) found.push("metadata");
    if (form.metadataSource === "xml" && !form.idpMetadataXml.trim().startsWith("<")) found.push("metadata");
  } else {
    if (!/^ldaps?:\/\//.test(form.ldapUrl.trim())) found.push("ldap_url");
    if (!form.bindDn.trim()) found.push("bind_dn");
    if (!form.baseDn.trim()) found.push("base_dn");
    if (!editing && !form.bindPassword) found.push("bind_password");
  }
  return found;
}

/** Plain LDAP without StartTLS: the module refuses to send passwords over it (outside the test bench). */
export function plainLdap(form: ProviderForm): boolean {
  return form.protocol === "ldap" && form.ldapUrl.trim().startsWith("ldap://") && !form.startTls;
}
