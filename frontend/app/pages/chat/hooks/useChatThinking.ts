/**
 * The thinking level a person keeps for each model: read from their preferences once per page,
 * saved with the preferences API (PATCH merges, so one model's entry does not touch the others).
 * Forgotten when who is signed in may change (``onAccessCleared``): the next person reads their own.
 */
import { useCallback, useEffect, useState } from "react";

import { preferences } from "~/api/preferences";
import { onAccessCleared } from "~/lib/adminConstants";
import { isThinkingLevel, type ThinkingLevel } from "../thinking";

type Kept = Record<string, ThinkingLevel>;

let cache: Kept | null = null;
let loading: Promise<Kept> | null = null;
/** Counts the resets: a read that started before one is not kept for the next person. */
let generation = 0;

function load(): Promise<Kept> {
  if (cache) return Promise.resolve(cache);
  const started = generation;
  loading ??= preferences
    .get()
    .then((res) => {
      const stored = res.preferences.chat_thinking ?? {};
      const kept: Kept = {};
      for (const [model, level] of Object.entries(stored)) {
        if (isThinkingLevel(level)) kept[model] = level;
      }
      if (started !== generation) return kept;
      // A level chosen while this was on its way wins over what was stored.
      cache = { ...kept, ...(cache ?? {}) };
      return cache;
    })
    .catch(() => {
      // Not saved yet, or not reachable: Auto everywhere; the next change still saves.
      if (started !== generation) return {};
      cache ??= {};
      return cache;
    })
    .finally(() => {
      if (started === generation) loading = null;
    });
  return loading;
}

/** Forget what was read: at sign-out and the like, and in tests. */
export function resetThinkingCache(): void {
  generation += 1;
  cache = null;
  loading = null;
}

onAccessCleared(resetThinkingCache);

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
