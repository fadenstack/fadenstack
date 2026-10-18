/**
 * Links back to fadenstack.com, tagged so the website's access log shows where a visit came from
 * (features/admin-guardrails' sibling: fadenstack-dev features/about-and-ref-links.md).
 *
 * A link carries `ref` (one value from {@link VendorRef}) and `v` (the release's first two numbers), nothing
 * else: never a user, tenant, host or installation name. Opened in a new tab without a Referer, so the
 * server's own address never leaves with it.
 */

export const WEBSITE = "https://fadenstack.com";
export const DEVELOPERS = "https://developers.fadenstack.com";
export const SOURCE_CODE = "https://github.com/fadenstack/fadenstack";

/** Where a link sits: the website counts these. */
export type VendorRef = "console-signin" | "console-about" | "console-updates" | "help";

/** The hosts a tag is added to; any other link is left as it is. */
const TAGGED_HOSTS = new Set(["fadenstack.com", "www.fadenstack.com", "help.fadenstack.com", "developers.fadenstack.com"]);

/** `0.4.5` -> `0.4`; `0.2.0-rc.3` -> `0.2`; anything else -> undefined. */
export function majorMinor(version: string | null | undefined): string | undefined {
  const match = /^v?(\d+)\.(\d+)/.exec((version ?? "").trim());
  return match ? `${match[1]}.${match[2]}` : undefined;
}

/** *url* with `ref` and, when known, `v`, if it points at one of Fadenstack's sites; otherwise unchanged. */
export function tagVendorUrl(url: string, ref: VendorRef, version?: string | null): string {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return url;
  }
  if (parsed.protocol !== "https:" || !TAGGED_HOSTS.has(parsed.hostname)) return url;
  parsed.searchParams.set("ref", ref);
  const v = majorMinor(version);
  if (v) parsed.searchParams.set("v", v);
  else parsed.searchParams.delete("v");
  return parsed.toString();
}

/** A page of fadenstack.com (`/`, `/releases/`, `/security/`), tagged. */
export function websiteUrl(path: string, ref: VendorRef, version?: string | null): string {
  return tagVendorUrl(`${WEBSITE}${path.startsWith("/") ? path : `/${path}`}`, ref, version);
}

/** What an outgoing link to another site needs: a new tab, no opener, no Referer. */
export const EXTERNAL_LINK = { target: "_blank", rel: "noopener noreferrer" } as const;
