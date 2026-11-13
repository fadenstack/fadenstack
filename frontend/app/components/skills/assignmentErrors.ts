/** The assignment refusals the console says in its own words; anything else keeps the server's message. */
import type { TFunction } from "i18next";

import { ApiError } from "~/api/http";
import { ASSIGNMENT_ERRORS } from "~/api/skills";

/** `work`, with a known refusal (`skills.error_<code>`) thrown as its text, naming the ids it lists. */
export async function sayingAssignmentErrors<T>(t: TFunction, work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (err) {
    if (err instanceof ApiError && (ASSIGNMENT_ERRORS as readonly (string | undefined)[]).includes(err.code)) {
      const ids = (err.detail as { ids?: unknown } | undefined)?.ids;
      throw new Error(t(`skills.error_${err.code}`, { ids: Array.isArray(ids) ? ids.join(", ") : "" }));
    }
    throw err;
  }
}

/** Whether two id lists hold the same ids, in any order. */
export function sameIds(a: string[], b: string[]): boolean {
  const set = new Set(a);
  return a.length === b.length && b.every((id) => set.has(id));
}
