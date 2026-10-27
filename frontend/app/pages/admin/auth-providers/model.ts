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
/** The address people open this server at: scheme, host and port, nothing after them. */
export const SERVER_ADDRESS_PATTERN = /^https?:\/\/[^\s/?#]+$/;
/** A SAML entity ID: an absolute URI of at most 256 characters (SAML2Int SDP-G04). */
const ENTITY_ID_PATTERN = /^(?:https?:\/\/|urn:)\S{1,250}$/;

export interface ProviderForm {
  key: string;
  name: string;
  enabled: boolean;
  protocol: Protocol;
  /** Where people open this server; the provider sends them back there (OIDC, SAML). */
  serverAddress: string;
  // OIDC
  issuer: string;
  clientId: string;
  clientSecret: string;
  scopes: string;
  userinfo: OIDCSettings["userinfo"];
  /** The claim that names the person; `sub` unless the provider keeps another across its apps. */
  subjectClaim: string;
  // SAML
  metadataSource: "url" | "xml" | "mdq";
  idpMetadataUrl: string;
  idpMetadataXml: string;
  /** MDQ: the service's address and the identity provider's entity ID. */
  idpMdqUrl: string;
  idpEntityId: string;
  /** The certificate the metadata must be signed with; empty: none pinned (required with MDQ). */
  metadataSigningCert: string;
  logoutAtIdp: boolean;
  logoutFromIdp: boolean;
  /** Empty: the metadata address. */
  spEntityId: string;
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
  /** The person's stable ID in the directory; empty: the directory type's (objectGUID, entryUUID). */
  subjectAttribute: string;
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
  provisionedOnly: boolean;
}

function server(address: string): string {
  return address.trim().replace(/\/+$/, "");
}

/** The addresses the provider is set up with: the callback (OIDC), the ACS and the metadata (SAML). */
export function addresses(key: string, serverAddress: string) {
  const base = `${server(serverAddress)}/api/auth/sso/${key || "<key>"}`;
  return {
    callback: `${base}/callback`, acs: `${base}/acs`, metadata: `${base}/metadata`, sls: `${base}/sls`,
  };
}

/** The server address in a stored callback or ACS address: what comes before `/api/auth/sso/`. */
function serverOf(address: string | null | undefined): string {
  if (!address) return "";
  const at = address.indexOf("/api/auth/sso/");
  if (at > 0) return address.slice(0, at);
  try {
    return new URL(address).origin;
  } catch {
    return "";
  }
}

/** A new provider's form; *serverAddress* is where the console is open now, unless the admin changes it. */
export function blankForm(protocol: Protocol = "oidc", serverAddress = ""): ProviderForm {
  return {
    key: "", name: "", enabled: true, protocol, serverAddress,
    issuer: "", clientId: "", clientSecret: "", scopes: "openid email profile", userinfo: "when_missing",
    subjectClaim: "sub",
    metadataSource: "url", idpMetadataUrl: "", idpMetadataXml: "", spEntityId: "", emailVerifiedByIdp: false,
    signRequests: false, idpMdqUrl: "", idpEntityId: "", metadataSigningCert: "", logoutAtIdp: false,
    logoutFromIdp: false,
    ldapUrl: "", startTls: false, caBundle: "", bindDn: "", bindPassword: "", baseDn: "", preset: "ad",
    userFilter: "", subjectAttribute: "", checkPeople: true,
    createAccount: true, defaultRoleIds: [], allowedDomains: "", manageGroups: false, groupRules: [],
    sessionHours: "12", inactiveDays: "45", ssoOnly: false, provisionedOnly: false,
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
    provisionedOnly: policy.provisioned_only ?? false,
  });
  const s = provider.settings;
  if (s.protocol === "oidc") {
    Object.assign(form, {
      serverAddress: serverOf(s.redirect_uri), issuer: s.issuer, clientId: s.client_id, scopes: s.scopes.join(" "),
      userinfo: s.userinfo, subjectClaim: s.subject_claim ?? "sub",
    });
  } else if (s.protocol === "saml") {
    const serverAddress = serverOf(s.acs_url);
    Object.assign(form, {
      serverAddress, idpMetadataUrl: s.idp_metadata_url ?? "",
      metadataSource: s.idp_mdq_url ? "mdq" : s.idp_metadata_url ? "url" : "xml",
      idpMetadataXml: s.idp_metadata_xml ?? "", emailVerifiedByIdp: s.email_verified_by_idp,
      signRequests: s.sign_requests, idpMdqUrl: s.idp_mdq_url ?? "", idpEntityId: s.idp_entity_id ?? "",
      metadataSigningCert: s.idp_metadata_signing_certificate ?? "", logoutAtIdp: s.logout_at_idp ?? false,
      logoutFromIdp: s.logout_from_idp ?? false,
      // The default entity ID stays the default, so it follows a changed server address.
      spEntityId: s.sp_entity_id === addresses(provider.key, serverAddress).metadata ? "" : s.sp_entity_id,
    });
  } else {
    Object.assign(form, {
      ldapUrl: s.url, startTls: s.start_tls, caBundle: s.ca_bundle ?? "", bindDn: s.bind_dn, baseDn: s.base_dn,
      preset: s.preset, userFilter: s.user_filter ?? "", subjectAttribute: s.subject_attribute ?? "",
      emailVerifiedByIdp: s.email_verified_by_idp,
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
    provisioned_only: form.provisionedOnly,
  };
}

function settingsOf(form: ProviderForm, stored?: ProviderSettings): ProviderWrite["settings"] {
  const here = addresses(form.key, form.serverAddress);
  if (form.protocol === "oidc") {
    const base = stored?.protocol === "oidc" ? stored : undefined;
    const settings: Partial<OIDCSettings> = {
      ...base, protocol: "oidc", issuer: form.issuer.trim(), client_id: form.clientId.trim(),
      redirect_uri: here.callback, scopes: form.scopes.split(/\s+/).filter(Boolean), userinfo: form.userinfo,
      subject_claim: form.subjectClaim.trim() || "sub",
    };
    return settings as ProviderWrite["settings"];
  }
  if (form.protocol === "saml") {
    const base = stored?.protocol === "saml" ? stored : undefined;
    const source = form.metadataSource;
    const settings: Partial<SAMLSettings> = {
      ...base, protocol: "saml",
      idp_metadata_url: source === "url" ? form.idpMetadataUrl.trim() : null,
      idp_metadata_xml: source === "xml" ? form.idpMetadataXml.trim() : null,
      idp_mdq_url: source === "mdq" ? form.idpMdqUrl.trim() : null,
      idp_entity_id: source === "mdq" ? form.idpEntityId.trim() : (base?.idp_entity_id ?? null),
      idp_metadata_signing_certificate: form.metadataSigningCert.trim() || null,
      sp_entity_id: form.spEntityId.trim() || here.metadata, acs_url: here.acs,
      email_verified_by_idp: form.emailVerifiedByIdp, sign_requests: form.signRequests,
      logout_at_idp: form.logoutAtIdp, logout_from_idp: form.logoutFromIdp, slo_url: here.sls,
    };
    return settings as ProviderWrite["settings"];
  }
  const base = stored?.protocol === "ldap" ? stored : undefined;
  const settings: Partial<LDAPSettings> = {
    ...base, protocol: "ldap", url: form.ldapUrl.trim(), start_tls: form.startTls,
    ca_bundle: form.caBundle.trim() || null, bind_dn: form.bindDn.trim(), base_dn: form.baseDn.trim(),
    preset: form.preset, user_filter: form.userFilter.trim() || null,
    subject_attribute: form.subjectAttribute.trim() || null,
    email_verified_by_idp: form.emailVerifiedByIdp, check_people: form.checkPeople,
  };
  return settings as ProviderWrite["settings"];
}

/** What the form sends: a new provider, or the changes to *stored*. Secrets only when typed. */
export function toWrite(form: ProviderForm, stored?: SignInProvider): ProviderWrite {
  const secrets: ProviderWrite["secrets"] = {};
  if (form.protocol === "oidc" && form.clientSecret) secrets.client_secret = form.clientSecret;
  if (form.protocol === "ldap" && form.bindPassword) secrets.bind_password = form.bindPassword;
  const write: ProviderWrite = {
    name: form.name.trim(),
    enabled: form.enabled,
    settings: settingsOf(form, stored?.settings),
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
  if (form.protocol !== "ldap" && !SERVER_ADDRESS_PATTERN.test(server(form.serverAddress))) found.push("server_address");
  if (form.protocol === "oidc") {
    if (!/^https?:\/\//.test(form.issuer.trim())) found.push("issuer");
    if (!form.clientId.trim()) found.push("client_id");
    if (!editing && !form.clientSecret) found.push("client_secret");
  } else if (form.protocol === "saml") {
    if (form.metadataSource === "url" && !/^https?:\/\//.test(form.idpMetadataUrl.trim())) found.push("metadata");
    if (form.metadataSource === "xml" && !form.idpMetadataXml.trim().startsWith("<")) found.push("metadata");
    if (form.metadataSource === "mdq") {
      if (!/^https?:\/\//.test(form.idpMdqUrl.trim())) found.push("metadata");
      if (!ENTITY_ID_PATTERN.test(form.idpEntityId.trim())) found.push("idp_entity_id");
      if (!form.metadataSigningCert.includes("BEGIN CERTIFICATE")) found.push("signing_certificate");
    } else if (form.metadataSigningCert.trim() && !form.metadataSigningCert.includes("BEGIN CERTIFICATE")) {
      found.push("signing_certificate");
    }
    if (form.spEntityId.trim() && !ENTITY_ID_PATTERN.test(form.spEntityId.trim())) found.push("entity_id");
  } else {
    if (!/^ldaps?:\/\//.test(form.ldapUrl.trim())) found.push("ldap_url");
    if (!form.bindDn.trim()) found.push("bind_dn");
    if (!form.baseDn.trim()) found.push("base_dn");
    if (!editing && !form.bindPassword) found.push("bind_password");
  }
  return found;
}

/** A server address without TLS: the sign-in module refuses to send people back over plain HTTP. */
export function plainServerAddress(form: ProviderForm): boolean {
  return form.protocol !== "ldap" && server(form.serverAddress).startsWith("http://");
}

/** Plain LDAP without StartTLS: the module refuses to send passwords over it (outside the test bench). */
export function plainLdap(form: ProviderForm): boolean {
  return form.protocol === "ldap" && form.ldapUrl.trim().startsWith("ldap://") && !form.startTls;
}
