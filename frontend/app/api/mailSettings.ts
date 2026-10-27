/**
 * Mail (`/api/ee/mail`, for administrators): how this server sends mail through a mail server on another
 * machine, a test mail, and whether mail goes out. Offered while the server lists the feature
 * {@link MAIL_SETTINGS} (`GET /api/about`).
 *
 * The password and the Microsoft 365 client secret are never sent back, only whether one is stored. A save
 * sends `null` for a secret to keep the stored one; a stored secret goes only to the server (host:port) it was
 * saved for, so a form that points elsewhere must bring it again (`again_for_new_server`).
 */
import { ApiError, json, requester } from "./http";

const request = requester("/api/ee/mail");

/** The feature the Mail page shows for. */
export const MAIL_SETTINGS = "mail_settings";

export type MailSecurity = "starttls" | "tls";
export type MailAuth = "none" | "password" | "microsoft365";

/** The saved settings; secrets only as whether one is stored. */
export interface MailSettings {
  host: string;
  port: number;
  security: MailSecurity;
  auth: MailAuth;
  username: string;
  password_set: boolean;
  ca_bundle: string;
  helo_name: string;
  from_email: string;
  from_name: string;
  link_base: string;
  admin_recipients: string[];
  m365_tenant_id: string;
  m365_client_id: string;
  m365_client_secret_set: boolean;
  /** The server (host:port) the stored secrets were saved for. */
  secrets_server: string;
}

/** A trusted certificate as read from the saved PEM text. */
export interface MailCertificate {
  subject: string;
  issuer: string;
  not_after: string;
  expired: boolean;
  /** A CA without key usage (RFC 5280): the mail service refuses it, as it may not sign certificates. */
  ca_without_key_usage: boolean;
}

/** Why no mail goes out. */
export type MailStopReason = "licence" | "module_off" | "no_server" | "no_link_base";

/** The newest mail that did not go out and why (never its addresses). */
export interface MailFailure {
  kind: string;
  code: string;
  reason: string;
  attempts: number;
  gave_up: boolean;
  created_at: string;
  next_attempt_at: string;
  /** It failed after the last mail that went out: mail is failing now. */
  after_last_sent: boolean;
}

export interface MailState {
  goes_out: boolean;
  reasons: MailStopReason[];
  waiting: number;
  last_sent_at: string | null;
  last_failure: MailFailure | null;
}

export interface MailView {
  settings: MailSettings;
  /** `null`: the saved PEM text holds no certificate that can be read. */
  certificates: MailCertificate[] | null;
  state: MailState;
  /** Root mode is active: a stored secret may be changed. */
  root_mode: boolean;
}

/** The whole form, as a save (and an unsaved test) sends it. `null` for a secret keeps the stored one. */
export interface MailForm {
  host: string;
  port: number;
  security: MailSecurity;
  auth: MailAuth;
  username: string;
  password: string | null;
  ca_bundle: string;
  helo_name: string;
  from_email: string;
  from_name: string;
  link_base: string;
  admin_recipients: string[];
  m365_tenant_id: string;
  m365_client_id: string;
  m365_client_secret: string | null;
}

export interface MailTestResult {
  ok: boolean;
  /** One of {@link MAIL_FAILURES} when it failed. */
  code: string | null;
  /** The mail server's own words. */
  reason: string;
  tls_version: string;
  auth: MailAuth | "";
  elapsed_ms: number | null;
}

/** The mail service's failure codes, each worded in the console. */
export const MAIL_FAILURES = [
  "not_configured",
  "dns_failed",
  "connect_failed",
  "timeout",
  "tls_unavailable",
  "tls_untrusted",
  "tls_expired",
  "tls_name_mismatch",
  "tls_failed",
  "greeting_refused",
  "login_unsupported",
  "login_required",
  "login_refused",
  "secret_for_other_server",
  "oauth_failed",
  "sender_refused",
  "relay_refused",
  "recipient_refused",
  "message_refused",
  "server_error",
  "mailer_unreachable",
] as const;

/** What is wrong with one field of the form. */
export interface MailProblem {
  field: string;
  /** `required`, `invalid`, `not_https`, `again_for_new_server`, `root_mode`. */
  code: string;
}

/** The problems per field a refused save or test names (422, 403); none for any other error. */
export function mailProblems(err: unknown): MailProblem[] {
  if (!(err instanceof ApiError) || !err.detail || typeof err.detail !== "object") return [];
  const found = (err.detail as { problems?: unknown }).problems;
  if (!Array.isArray(found)) return [];
  return found.filter(
    (p): p is MailProblem =>
      !!p && typeof p === "object" && typeof (p as MailProblem).field === "string" && typeof (p as MailProblem).code === "string",
  );
}

/** Too many test mails in ten minutes (429). */
export function tooManyTests(err: unknown): boolean {
  return err instanceof ApiError && err.status === 429;
}

/** The settings keys the Mail page sets; the Modules tab's mailer card leaves them to it. */
export function isMailPageSettingKey(key: string): boolean {
  return (
    key.startsWith("faden_mailer.smtp.") ||
    key.startsWith("faden_mailer.m365.") ||
    [
      "faden_mailer.from_email",
      "faden_mailer.from_name",
      "faden_mailer.frontend_base_url",
      "faden_mailer.admin_recipients",
    ].includes(key)
  );
}

export const mailApi = {
  get: () => request<MailView>(""),
  /** The whole form in one save; nothing is written when anything in it is wrong. */
  save: (form: MailForm) => request<MailView>("", json("PUT", form)),
  /** A test mail to *to*, with the form's (unsaved) settings, or the saved ones without. */
  test: (to: string, settings?: MailForm) =>
    request<MailTestResult>("/test", json("POST", settings ? { to, settings } : { to })),
};
