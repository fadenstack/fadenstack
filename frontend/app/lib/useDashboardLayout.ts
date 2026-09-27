/**
 * The signed-in user's dashboard layout: server-saved with revisions, mirrored
 * locally for an instant first paint.
 *
 * The server numbers saves (see the backend's services/dashboard/layout.py);
 * a save based on an old revision comes back 409 with the stored layout,
 * which this hook adopts, keeping the user's version on hand for "Keep mine".
 * No client clock is consulted.
 *
 * The local mirror (`faden-dashboard-layout-v2:<user>`) holds the layout,
 * the revision it was based on and whether it has unsaved edits. It is per
 * user: the v1 key was not, so on a shared browser one admin's layout showed
 * up for the next. A `storage` event keeps tabs of the same browser in step.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { dashboardLayout, type DashboardLayout } from "~/api/dashboard";
import {
  defaultLayout,
  migrateV1,
  reconcile,
  type LayoutWidgetSpec,
  type Profile,
} from "~/lib/dashboardLayoutModel";

const MIRROR_PREFIX = "faden-dashboard-layout-v2:";
const V1_KEY = "faden-dashboard-layout";
const SAVE_DEBOUNCE_MS = 800;

interface Mirror {
  layout: DashboardLayout;
  base_revision: number;
  dirty: boolean;
}

function readJson(key: string): unknown {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as unknown) : null;
  } catch {
    return null;
  }
}

function readMirror(key: string): Mirror | null {
  const value = readJson(key) as Partial<Mirror> | null;
  if (!value || typeof value !== "object" || !value.layout || typeof value.base_revision !== "number") {
    return null;
  }
  return { layout: value.layout, base_revision: value.base_revision, dirty: Boolean(value.dirty) };
}

function readV1(): { order: string[]; hidden: string[] } | null {
  const value = readJson(V1_KEY) as { order?: unknown; hidden?: unknown } | null;
  if (value && Array.isArray(value.order) && Array.isArray(value.hidden)) {
    return { order: value.order as string[], hidden: value.hidden as string[] };
  }
  return null;
}

export interface DashboardLayoutState {
  /** Null until known: no local mirror and the server has not answered yet. */
  layout: DashboardLayout | null;
  update: (edit: (layout: DashboardLayout) => DashboardLayout) => void;
  reset: () => void;
  /** The user's layout, when another device's save replaced it. */
  conflict: DashboardLayout | null;
  keepMine: () => void;
  dismissConflict: () => void;
  saveError: string | null;
}

export function useDashboardLayout(
  specs: LayoutWidgetSpec[],
  { userKey, profile }: { userKey: string; profile: Profile },
): DashboardLayoutState {
  const mirrorKey = `${MIRROR_PREFIX}${userKey}`;
  const specsRef = useRef(specs);
  specsRef.current = specs;

  const initial = useRef<Mirror | null | undefined>(undefined);
  if (initial.current === undefined) initial.current = typeof window === "undefined" ? null : readMirror(mirrorKey);

  const [layout, setLayoutState] = useState<DashboardLayout | null>(() =>
    initial.current ? reconcile(initial.current.layout, specs, profile) : null,
  );
  const [conflict, setConflict] = useState<DashboardLayout | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const layoutRef = useRef(layout);
  const baseRevision = useRef(initial.current?.base_revision ?? 0);
  const dirty = useRef(initial.current?.dirty ?? false);
  const editSeq = useRef(0);
  const inflight = useRef(false);
  const pending = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const migratedV1 = useRef(false);

  const persist = useCallback(() => {
    if (!layoutRef.current) return;
    try {
      const mirror: Mirror = {
        layout: layoutRef.current,
        base_revision: baseRevision.current,
        dirty: dirty.current,
      };
      window.localStorage.setItem(mirrorKey, JSON.stringify(mirror));
    } catch {
      // storage full or blocked: the server copy still has it
    }
  }, [mirrorKey]);

  const setLayout = useCallback((next: DashboardLayout) => {
    layoutRef.current = next;
    setLayoutState(next);
  }, []);

  const adopt = useCallback(
    (stored: { layout: DashboardLayout | null; revision: number }) => {
      baseRevision.current = stored.revision;
      dirty.current = false;
      setLayout(reconcile(stored.layout, specsRef.current, profile));
      persist();
    },
    [persist, profile, setLayout],
  );

  const save = useCallback(async () => {
    if (!layoutRef.current) return;
    if (inflight.current) {
      pending.current = true;
      return;
    }
    const seq = editSeq.current;
    const mine = layoutRef.current;
    inflight.current = true;
    try {
      const result = await dashboardLayout.save(mine, baseRevision.current);
      if (result.ok) {
        baseRevision.current = result.stored.revision;
        if (editSeq.current === seq) dirty.current = false;
        persist();
        setSaveError(null);
        if (migratedV1.current) {
          migratedV1.current = false;
          try {
            window.localStorage.removeItem(V1_KEY);
          } catch {
            // ignore
          }
        }
      } else {
        // Another device saved first: show theirs, keep ours for "Keep mine".
        setConflict(mine);
        adopt(result.stored);
      }
    } catch (err) {
      // Still dirty in the mirror: retried on the next edit or page load.
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      inflight.current = false;
      if (pending.current) {
        pending.current = false;
        void save();
      }
    }
  }, [adopt, persist]);

  const update = useCallback(
    (edit: (current: DashboardLayout) => DashboardLayout) => {
      const current = layoutRef.current ?? defaultLayout(specsRef.current, profile);
      const next = edit(current);
      if (next === current) return;
      editSeq.current += 1;
      dirty.current = true;
      setLayout(next);
      persist();
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void save(), SAVE_DEBOUNCE_MS);
    },
    [persist, profile, save, setLayout],
  );

  // Fetch the server copy once per user.
  useEffect(() => {
    let cancelled = false;
    dashboardLayout
      .get()
      .then((stored) => {
        if (cancelled) return;
        if (dirty.current) {
          void save(); // unsaved edits from last time, based on the mirror's revision
        } else if (stored.layout && stored.revision >= baseRevision.current) {
          adopt(stored);
        } else if (!stored.layout) {
          const v1 = readV1();
          if (v1) {
            migratedV1.current = true;
            update(() => migrateV1(v1, specsRef.current, profile));
          } else if (!layoutRef.current) {
            setLayout(defaultLayout(specsRef.current, profile));
          }
        }
      })
      .catch(() => {
        if (!cancelled && !layoutRef.current) setLayout(defaultLayout(specsRef.current, profile));
      });
    return () => {
      cancelled = true;
    };
    // Once per user; `save`/`adopt`/`update` are stable for a given user and profile.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mirrorKey]);

  // Flush a pending save when the page goes away.
  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
        if (dirty.current) void save();
      }
    },
    [save],
  );

  // Another tab of this browser saved.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== mirrorKey || !event.newValue || dirty.current) return;
      const mirror = readMirror(mirrorKey);
      if (mirror) {
        baseRevision.current = mirror.base_revision;
        setLayout(reconcile(mirror.layout, specsRef.current, profile));
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [mirrorKey, profile, setLayout]);

  const reset = useCallback(() => update(() => defaultLayout(specsRef.current, profile)), [profile, update]);

  const keepMine = useCallback(() => {
    const mine = conflict;
    setConflict(null);
    if (mine) update(() => mine);
  }, [conflict, update]);

  const dismissConflict = useCallback(() => setConflict(null), []);

  return { layout, update, reset, conflict, keepMine, dismissConflict, saveError };
}
