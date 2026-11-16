/**
 * Whether this install may generate pictures, read from the server.
 *
 * The answer comes only from the server. While the request is open, and if it
 * fails, the console treats image generation as off: it must never offer
 * picture hosting on a guess.
 */
import { marketplaceApi } from "~/api/marketplace";

import { useAsyncData } from "./useAsyncData";

export function useImageGeneration(): boolean {
  const { data } = useAsyncData(
    () =>
      marketplaceApi
        .imageGeneration()
        .then((r) => r.enabled === true)
        .catch(() => false),
    [],
    { initialValue: false },
  );
  return data;
}
