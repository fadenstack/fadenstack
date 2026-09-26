/**
 * Whether the Skills module is switched on, for pages outside the admin
 * layout (the chat, the user's skill pages), which have no ServicesContext.
 */
import { useEffect, useState } from "react";

import { servicesApi } from "~/api/services";

export function useSkillsModule(): { enabled: boolean; loading: boolean } {
  const [state, setState] = useState({ enabled: false, loading: true });
  useEffect(() => {
    let cancelled = false;
    servicesApi
      .list()
      .then((manifest) => {
        if (cancelled) return;
        const skills = manifest.services.find((s) => s.name === "skills");
        setState({ enabled: Boolean(skills?.enabled), loading: false });
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
