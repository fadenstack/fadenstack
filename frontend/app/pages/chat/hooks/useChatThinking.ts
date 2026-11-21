/**
 * The thinking level a person keeps for each model: read from their preferences once per page,
 * saved with the preferences API (PATCH merges, so one model's entry does not touch the others).
 */
import { useCallback, useEffect, useState } from "react";

import { preferences } from "~/api/preferences";
import { isThinkingLevel, type ThinkingLevel } from "../thinking";

type Kept = Record<string, ThinkingLevel>;

let cache: Kept | null = null;
let loading: Promise<Kept> | null = null;

function load(): Promise<Kept> {
  if (cache) return Promise.resolve(cache);
  loading ??= preferences
    .get()
    .then((res) => {
      const stored = res.preferences.chat_thinking ?? {};
      const kept: Kept = {};
      for (const [model, level] of Object.entries(stored)) {
        if (isThinkingLevel(level)) kept[model] = level;
      }
      // A level chosen while this was on its way wins over what was stored.
      cache = { ...kept, ...(cache ?? {}) };
      return cache;
    })
    .catch(() => {
      // Not saved yet, or not reachable: Auto everywhere; the next change still saves.
      cache ??= {};
      return cache;
    })
    .finally(() => {
      loading = null;
    });
  return loading;
}

/** For tests: forget what was read. */
export function resetThinkingCache(): void {
  cache = null;
  loading = null;
}

export function useChatThinking(): {
  kept: Kept;
  choose: (model: string, level: ThinkingLevel) => void;
} {
  const [kept, setKept] = useState<Kept>(cache ?? {});

  useEffect(() => {
    let alive = true;
    void load().then((value) => {
      if (alive) setKept({ ...value });
    });
    return () => {
      alive = false;
    };
  }, []);

  const choose = useCallback((model: string, level: ThinkingLevel) => {
    cache = { ...(cache ?? {}), [model]: level };
    setKept(cache);
    void preferences.update({ chat_thinking: { [model]: level } }).catch(() => undefined);
  }, []);

  return { kept, choose };
}
