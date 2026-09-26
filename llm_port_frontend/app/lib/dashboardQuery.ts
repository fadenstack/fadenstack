/**
 * Shared query cache for dashboard widgets.
 *
 * Each widget owns its data lifecycle -- when it loads, how often it
 * refreshes, its own error and stale states -- but several widgets read the
 * same endpoint (the overview feeds both Platform health and Server
 * resources). A query here is fetched once and shared: two widgets asking for
 * one key at once share one request, and a widget that mounts while another's
 * copy is fresh uses it. A failure reaches only the widgets that read that key.
 *
 * The cache belongs to one user. `setDashboardQueryOwner` empties it whenever
 * the signed-in user changes, so one admin never sees another's cached
 * figures on a shared browser.
 */
import { useCallback, useEffect, useRef, useState } from "react";

interface Entry {
  data?: unknown;
  error?: string;
  updatedAt?: number;
  failures: number;
  inflight?: Promise<void>;
  listeners: Set<() => void>;
}

const entries = new Map<string, Entry>();
let owner: string | null = null;

/** Empty the cache when the signed-in user changes (or on logout, with null). */
export function setDashboardQueryOwner(user: string | null): void {
  if (user === owner) return;
  owner = user;
  entries.clear();
}

/** Forget everything (tests). */
export function clearDashboardQueries(): void {
  entries.clear();
}

function entryFor(key: string): Entry {
  let entry = entries.get(key);
  if (!entry) {
    entry = { failures: 0, listeners: new Set() };
    entries.set(key, entry);
  }
  return entry;
}

function notify(entry: Entry): void {
  for (const listener of entry.listeners) listener();
}

function run(key: string, fetcher: () => Promise<unknown>): Promise<void> {
  const entry = entryFor(key);
  if (entry.inflight) return entry.inflight;
  entry.inflight = fetcher()
    .then(
      (data) => {
        entry.data = data;
        entry.error = undefined;
        entry.updatedAt = Date.now();
        entry.failures = 0;
      },
      (err: unknown) => {
        entry.error = err instanceof Error ? err.message : String(err);
        entry.failures += 1;
      },
    )
    .finally(() => {
      entry.inflight = undefined;
      notify(entry);
    });
  notify(entry);
  return entry.inflight;
}

export interface QueryState<T> {
  data: T | undefined;
  /** The last error; kept alongside older data, which stays on screen. */
  error: string | null;
  /** True until the first answer (data or error) arrives. */
  loading: boolean;
  /** When the data was fetched (ms since epoch). */
  updatedAt: number | null;
  /** True when the data is older than three refresh intervals. */
  stale: boolean;
  refresh: () => Promise<void>;
}

export interface QueryOptions {
  /** Poll interval. Failures back off (x2, up to x4); polling pauses while the tab is hidden. */
  refreshMs: number;
  /** When false the query neither fetches nor polls (a hidden widget). */
  enabled?: boolean;
  /** Bumped by the page's Refresh button to force a fetch. */
  refreshToken?: number;
}

/**
 * Subscribe a widget to a shared query.
 *
 * `key` must identify everything the fetcher depends on (path and
 * parameters); the fetcher of whichever widget asks first is the one used.
 */
export function useDashboardQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  { refreshMs, enabled = true, refreshToken = 0 }: QueryOptions,
): QueryState<T> {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [, force] = useState(0);

  const refresh = useCallback(() => run(key, () => fetcherRef.current()), [key]);

  // Re-render whenever this key's entry changes.
  useEffect(() => {
    const entry = entryFor(key);
    const listener = () => force((n) => n + 1);
    entry.listeners.add(listener);
    return () => {
      entry.listeners.delete(listener);
    };
  }, [key]);

  // First fetch, polling with backoff, and catching up when the tab returns.
  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    const entry = entryFor(key);
    const isFresh = () => entry.updatedAt !== undefined && Date.now() - entry.updatedAt < refreshMs * 0.9;
    const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

    const schedule = () => {
      if (cancelled) return;
      const delay = refreshMs * Math.min(2 ** entry.failures, 4);
      timer = setTimeout(tick, delay);
    };
    const tick = () => {
      if (cancelled) return;
      if (hidden() || isFresh()) {
        schedule();
        return;
      }
      void run(key, () => fetcherRef.current()).then(schedule);
    };

    if (isFresh()) schedule();
    else void run(key, () => fetcherRef.current()).then(schedule);

    const onVisible = () => {
      if (!hidden() && !isFresh()) void run(key, () => fetcherRef.current());
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [key, enabled, refreshMs]);

  // The page's Refresh button.
  const firstToken = useRef(refreshToken);
  useEffect(() => {
    if (!enabled || refreshToken === firstToken.current) return;
    void refresh();
  }, [refreshToken, enabled, refresh]);

  const entry = entryFor(key);
  const updatedAt = entry.updatedAt ?? null;
  return {
    data: entry.data as T | undefined,
    error: entry.error ?? null,
    loading: entry.data === undefined && entry.error === undefined,
    updatedAt,
    stale: updatedAt !== null && Date.now() - updatedAt > refreshMs * 3,
    refresh,
  };
}
