/**
 * Clients for the redesigned dashboard's endpoints (/api/admin/dashboard/*).
 *
 * The older ones -- overview, health, top containers -- stay in `api/admin.ts`.
 * Everything here is superuser-only on the server.
 */
import { clearCachedAccess } from "~/lib/adminConstants";

const BASE = "/api/admin/dashboard";

/** An HTTP error with its status and parsed body, for callers that act on them. */
export class DashboardApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) clearCachedAccess();
    const text = await res.text().catch(() => res.statusText);
    let body: unknown = text;
    try {
      body = JSON.parse(text);
    } catch {
      // not JSON; keep the text
    }
    throw new DashboardApiError(res.status, body, `API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

/** The viewer's IANA time zone; the server counts "today" and days in it. */
export function viewerTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

// ── Layout ──────────────────────────────────────────────────────────

export type WidgetSize = "S" | "M" | "L" | "XL";
export type TrafficWindow = "today" | "24h" | "7d" | "30d";
export type UsageRange = "7d" | "30d";
export type FleetColorBy = "health" | "gpu_util" | "vram_allocated";

export interface DashboardLayout {
  version: 2;
  order: string[];
  hidden: string[];
  sizes: Record<string, WidgetSize>;
  kpis: string[];
  traffic_window: TrafficWindow;
  usage_range: UsageRange;
  fleet_color_by: FleetColorBy;
}

export interface StoredLayout {
  layout: DashboardLayout | null;
  revision: number;
}

export type LayoutSaveResult =
  | { ok: true; stored: StoredLayout }
  | { ok: false; stored: StoredLayout };

export const dashboardLayout = {
  get() {
    return request<StoredLayout>("/layout");
  },

  /** Save; on 409 the server's stored layout comes back instead. */
  async save(layout: DashboardLayout, baseRevision: number): Promise<LayoutSaveResult> {
    try {
      const stored = await request<StoredLayout>("/layout", {
        method: "PUT",
        body: JSON.stringify({ layout, base_revision: baseRevision }),
      });
      return { ok: true, stored };
    } catch (err) {
      if (err instanceof DashboardApiError && err.status === 409) {
        return { ok: false, stored: err.body as StoredLayout };
      }
      throw err;
    }
  },
};
