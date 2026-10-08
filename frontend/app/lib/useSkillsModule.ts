/**
 * Whether the Skills module is switched on, for pages outside the admin
 * layout (the chat, the user's skill pages), which have no ServicesContext.
 *
 * It asks the skills API, which everyone who may use skills can. It used to
 * read the list of modules, which only administrators may: for every other
 * user skills looked switched off, and the chat had no skills at all.
 */
import { useEffect, useState } from "react";

import { skillsStatus } from "~/api/skills";

export function useSkillsModule(): { enabled: boolean; loading: boolean } {
  const [state, setState] = useState({ enabled: false, loading: true });
  useEffect(() => {
    let cancelled = false;
    skillsStatus()
      .then(({ enabled }) => {
        if (!cancelled) setState({ enabled: Boolean(enabled), loading: false });
      })
      .catch(() => {
        if (!cancelled) setState({ enabled: false, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}
