/**
 * Users and groups something is granted to. Skills and agents look them up
 * the same way (the backend's services/principals.py), each under its own route.
 */

export type PrincipalType = "user" | "group";

/** A user or group, as a search or a lookup by id returns it. */
export interface PrincipalOption {
  type: PrincipalType;
  id: string;
  label: string;
}

/** One stable key for a principal: `user:<id>`. */
export function principalKey(type: PrincipalType, id: string): string {
  return `${type}:${id}`;
}

/** The `ids` parameter naming principals: `user:<id>,group:<id>`. */
export function principalIds(principals: { type: PrincipalType; id: string }[]): string {
  return principals.map((p) => principalKey(p.type, p.id)).join(",");
}
