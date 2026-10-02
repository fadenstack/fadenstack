/**
 * Whether the signed-in user may do something: `can("llm.models:download")`.
 *
 * Read from the layout around the page, which already knows the user's
 * permissions: the admin layout passes `can`; the chat layout passes the
 * permission keys (`*` for a superuser). Outside both (a test rendering a
 * page on its own) everything is allowed: this only hides buttons, and the
 * server refuses what is not.
 */
import { useOutletContext } from "react-router";

export type Can = (permission?: string) => boolean;

const ALLOW_ALL: Can = () => true;

/** `can` over a set of permission keys, where `*` allows everything. */
export function canFrom(keys: Set<string>): Can {
  return (permission) => !permission || keys.has("*") || keys.has(permission);
}

export function useCan(): Can {
  const context = useOutletContext<{ can?: Can; permissions?: Set<string> } | undefined>();
  if (context?.can) return context.can;
  if (context?.permissions) return canFrom(context.permissions);
  return ALLOW_ALL;
}
