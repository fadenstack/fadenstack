/**
 * Names for the users a list refers to by id (events, sign-ins, decisions):
 * looked up once per distinct set, the id itself until the answer comes.
 */
import { useEffect, useMemo, useState } from "react";

import { principalKey, type PrincipalOption, type PrincipalType } from "~/api/principals";

type Namer = (principals: { type: PrincipalType; id: string }[]) => Promise<PrincipalOption[]>;

export function usePrincipalNames(userIds: (string | null | undefined)[], namePrincipals: Namer): (id: string) => string {
  const wanted = useMemo(
    () => [...new Set(userIds.filter((id): id is string => Boolean(id)))].sort().join(","),
    [userIds],
  );
  const [names, setNames] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    if (!wanted) return;
    let cancelled = false;
    namePrincipals(wanted.split(",").map((id) => ({ type: "user" as const, id })))
      .then((found) => {
        if (!cancelled) setNames(new Map(found.map((p) => [principalKey(p.type, p.id), p.label])));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [wanted, namePrincipals]);

  return (id: string) => names.get(principalKey("user", id)) ?? id;
}
