/**
 * User preferences API client — the dashboard's default profile, guide progress,
 * the getting-started list and the user's language.
 *
 * Endpoints:
 *   GET   /api/admin/users/me/preferences
 *   PATCH /api/admin/users/me/preferences
 */

import { clearCachedAccess } from "~/lib/adminConstants";

const BASE = "/api/admin/users";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      Pragma: "no-cache",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      clearCachedAccess();
    }
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Types ────────────────────────────────────────────────────────────

export interface TourProgress {
  version: number;
  current_step: number;
  completed: boolean;
  skipped_steps: number[];
  completed_at: string | null;
}

export interface UserPreferences {
  profile?: "private" | "team" | "enterprise" | null;
  /** A null value removes the entry (the server deletes keys set to null). */
  tour_progress?: Record<string, TourProgress | null> | null;
  getting_started?: { dismissed?: boolean; chat_opened?: boolean } | null;
  /** The chat's first-run tips. */
  chat_tips?: { dismissed?: boolean } | null;
  /**
   * The language the user picked (a language tag such as "de" or "zh-Hans"), for the console and
   * every agent of theirs; null forgets it.
   */
  language?: string | null;
}

export interface UserPreferencesResponse {
  preferences: UserPreferences;
}

// ── API ──────────────────────────────────────────────────────────────

export const preferences = {
  /** Fetch current user's preferences. */
  get() {
    return request<UserPreferencesResponse>("/me/preferences");
  },

  /** Merge-update the current user's preferences; a null value removes the key. */
  update(patch: Partial<UserPreferences>) {
    return request<UserPreferencesResponse>("/me/preferences", {
      method: "PATCH",
      body: JSON.stringify({ preferences: patch }),
    });
  },
};
