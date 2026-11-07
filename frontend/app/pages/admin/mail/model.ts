/**
 * The Mail page's form: what the inputs hold, how it is made from the saved settings, and what a save sends.
 * Secrets start empty; an empty one is sent as `null` (keep the stored one).
 */
import type { MailAuth, MailForm, MailProblem, MailSecurity, MailSettings } from "~/api/mailSettings";

export interface MailFormState {
  host: string;
  /** As typed: checked before it is sent. */
  port: string;
  security: MailSecurity;
  auth: MailAuth;
  username: string;
  /** Empty: keep the stored one. */
  password: string;
  ca_bundle: string;
  helo_name: string;
  from_email: string;
  from_name: string;
  link_base: string;
  admin_recipients: string[];
  m365_tenant_id: string;
  m365_client_id: string;
  /** Empty: keep the stored one. */
  m365_client_secret: string;
}

/** Each encryption's usual port. */
export const DEFAULT_PORT: Record<MailSecurity, string> = { starttls: "587", tls: "465" };

/** Where Microsoft 365 takes mail from applications (SMTP AUTH with OAuth); its sign-in goes nowhere else. */
export const M365_HOST = "smtp.office365.com";

/** The most alert recipients the server takes. */
export const MAX_RECIPIENTS = 50;

/** The longest text the server takes in each field. */
export const MAX_LENGTH = {
  host: 253,
  username: 320,
  password: 1024,
  ca_bundle: 200_000,
  helo_name: 253,
  from_email: 320,
  from_name: 200,
  link_base: 2048,
  m365_tenant_id: 100,
  m365_client_id: 100,
  m365_client_secret: 1024,
  to: 320,
} as const;

/** A host name as the server compares it: lower case, without a trailing dot. */
function hostName(host: string): string {
  return host.trim().toLowerCase().replace(/\.$/, "");
}

/** Whether *host* is Microsoft 365's (the only server its sign-in goes to). */
export function isM365Host(host: string): boolean {
  return hostName(host) === M365_HOST;
}

/** The server's address check, so an address is refused before it is added to a list. */
const ADDRESS = /^[^@\s<>"(),;:]+@[^@\s<>"(),;:]+\.[^@\s<>"(),;:]+$/;

export function isAddress(value: string): boolean {
  return ADDRESS.test(value.trim());
}

export function formFromSettings(s: MailSettings): MailFormState {
  return {
    host: s.host,
    port: String(s.port),
    security: s.security,
    auth: s.auth,
    username: s.username,
    password: "",
    ca_bundle: s.ca_bundle,
    helo_name: s.helo_name,
    from_email: s.from_email,
    from_name: s.from_name,
    link_base: s.link_base,
    admin_recipients: [...s.admin_recipients],
    m365_tenant_id: s.m365_tenant_id,
    m365_client_id: s.m365_client_id,
    m365_client_secret: "",
  };
}

/** The port as a number, or `null` when it is none (1-65535). */
export function portNumber(port: string): number | null {
  const text = port.trim();
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  return value >= 1 && value <= 65535 ? value : null;
}

/** What the form itself can tell is wrong before anything is sent. */
export function localProblems(form: MailFormState): MailProblem[] {
  const problems: MailProblem[] = [];
  if (portNumber(form.port) === null) problems.push({ field: "port", code: form.port.trim() ? "invalid" : "required" });
  // The Microsoft 365 sign-in is never sent to another server (the server refuses it too).
  if (form.auth === "microsoft365" && form.host.trim() && !isM365Host(form.host)) {
    problems.push({ field: "host", code: "m365_host" });
  }
  return problems;
}

/**
 * The request body: the whole form. A secret only for the sign-in that uses it, and only when typed; otherwise
 * `null`, which keeps the stored one.
 */
export function toBody(form: MailFormState): MailForm {
  return {
    host: form.host.trim(),
    port: portNumber(form.port) ?? 0,
    security: form.security,
    auth: form.auth,
    username: form.username.trim(),
    password: form.auth === "password" && form.password ? form.password : null,
    ca_bundle: form.ca_bundle,
    helo_name: form.helo_name.trim(),
    from_email: form.from_email.trim(),
    from_name: form.from_name,
    link_base: form.link_base.trim(),
    admin_recipients: form.admin_recipients,
    m365_tenant_id: form.m365_tenant_id.trim(),
    m365_client_id: form.m365_client_id.trim(),
    m365_client_secret: form.auth === "microsoft365" && form.m365_client_secret ? form.m365_client_secret : null,
  };
}

/**
 * The change to make when the encryption changes: the other encryption's usual port follows it, a port of
 * one's own stays.
 */
export function withSecurity(form: MailFormState, security: MailSecurity): Partial<MailFormState> {
  const port = form.port.trim();
  const usual = port === "" || Object.values(DEFAULT_PORT).includes(port);
  return usual ? { security, port: DEFAULT_PORT[security] } : { security };
}

/**
 * The change to make when the sign-in changes: Microsoft 365 always brings its server (smtp.office365.com, 587,
 * STARTTLS), as its sign-in goes nowhere else. The other sign-ins keep the server.
 */
export function withAuth(form: MailFormState, auth: MailAuth): Partial<MailFormState> {
  if (auth === "microsoft365") {
    return { auth, host: M365_HOST, port: DEFAULT_PORT.starttls, security: "starttls" };
  }
  return { auth };
}

/** The server a switch to *auth* replaces, to say so; `null` when none is replaced. */
export function replacedServer(form: MailFormState, auth: MailAuth): string | null {
  if (auth !== "microsoft365" || form.auth === "microsoft365") return null;
  const host = form.host.trim();
  return host && !isM365Host(host) ? host : null;
}

/** `host:port`, as the server compares it with the one the stored secrets were saved for. */
export function serverOf(form: MailFormState): string {
  return `${hostName(form.host)}:${portNumber(form.port) ?? form.port.trim()}`;
}

/** The problems by field (the first one each), for the fields' helper texts. */
export function byField(problems: MailProblem[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of problems) if (!(p.field in out)) out[p.field] = p.code;
  return out;
}
