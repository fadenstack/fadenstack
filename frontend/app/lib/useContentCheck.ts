/**
 * Whether the signed-in user can check content here, for pages outside the
 * admin layout (the chat's menu, the check page), which have no
 * ServicesContext: it asks `/api/provenance/status`, which everyone with
 * `provenance:check` may.
 *
 * on: the provenance module is on; off: it is switched off (or the status
 * cannot be read); denied: the user has no right to check.
 */
import { useEffect, useState } from "react";

import { ApiError } from "~/api/http";
import { fetchContentCheckStatus } from "~/api/provenance";

export type ContentCheckState = "loading" | "on" | "off" | "denied";

/** The largest content the backend checks, until it says otherwise. */
export const DEFAULT_MAX_BYTES = 20 * 1024 * 1024;

export function useContentCheck(allowed = true): { state: ContentCheckState; maxBytes: number } {
  const [status, setStatus] = useState<{ state: ContentCheckState; maxBytes: number }>({
    state: allowed ? "loading" : "denied",
    maxBytes: DEFAULT_MAX_BYTES,
  });
  useEffect(() => {
    if (!allowed) {
      setStatus({ state: "denied", maxBytes: DEFAULT_MAX_BYTES });
      return;
    }
    let cancelled = false;
    fetchContentCheckStatus()
      .then(({ enabled, max_bytes }) => {
        if (!cancelled) {
          setStatus({ state: enabled ? "on" : "off", maxBytes: max_bytes || DEFAULT_MAX_BYTES });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const denied = err instanceof ApiError && err.status === 403;
        setStatus({ state: denied ? "denied" : "off", maxBytes: DEFAULT_MAX_BYTES });
      });
    return () => {
      cancelled = true;
    };
  }, [allowed]);
  return status;
}
