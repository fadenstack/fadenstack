/**
 * Why a sign-in did not happen, in the person's language. The codes are the backend's refusals
 * (``services/sign_in/contract.py``, ``Refusal``); they reach the sign-in page as ``?error=<code>`` or as
 * a refused request's ``detail``. Only these are shown: anything else in the address reads as a plain
 * failure, so a crafted link cannot put its own words on the page.
 */
import type { TFunction } from "i18next";

export const REFUSALS = [
  "account_disabled",
  "account_not_verified",
  "registration_disabled",
  "email_missing",
  "email_not_trusted",
  "link_needs_confirmation",
  "wrong_credentials",
  "not_allowed",
  "provider_error",
  "provider_unavailable",
  "sign_in_expired",
  "needs_https",
  "license",
  "not_offered",
] as const;

export type RefusalCode = (typeof REFUSALS)[number];

export function isRefusal(code: string | null | undefined): code is RefusalCode {
  return !!code && (REFUSALS as readonly string[]).includes(code);
}

/** The text for *code*: its own, or the plain failure for one the backend does not name. */
export function refusalText(t: TFunction, code: string | null | undefined): string {
  return isRefusal(code) ? t(`auth.refused.${code}`) : t("auth.refused.other");
}
