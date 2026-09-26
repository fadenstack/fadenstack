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

// ── Traffic and key metrics ─────────────────────────────────────────

export type ResidencyClass = "machines" | "private" | "external" | "unknown";
export const RESIDENCY_CLASSES: ResidencyClass[] = ["machines", "private", "external", "unknown"];

/** An amount in one currency; amounts in different currencies are never added. */
export interface Money {
  currency: string;
  /** A decimal string ("42.18"). */
  amount: string;
}

export interface KpiItem {
  id: string;
  value: number | Money[] | null;
  previous: number | Money[] | null;
  context: Record<string, number | null | undefined>;
}

export interface KpisResponse {
  generated_at: string;
  items: KpiItem[];
  unavailable: string[];
}

export interface TrafficDestination {
  kind: "cluster" | "this_server" | "found" | "provider" | "unknown" | "other";
  id: string | null;
  name: string | null;
  residency: ResidencyClass | null;
  requests: number;
}

export interface TrafficResponse {
  generated_at: string;
  window: { start: string; end: string };
  requests: number;
  routed: number;
  tokens: number;
  errors: { count: number; rate: number | null };
  latency_ms: { p50: number | null; p95: number | null };
  by_residency: { residency: ResidencyClass; requests: number }[];
  by_destination: TrafficDestination[];
}

export interface SeriesBucket extends Record<ResidencyClass, number> {
  bucket: string;
  partial: boolean;
}

export interface TrafficSeriesResponse {
  generated_at: string;
  granularity: "day" | "hour";
  tz: string;
  buckets: SeriesBucket[];
}

function query(params: Record<string, string>): string {
  return `?${new URLSearchParams(params).toString()}`;
}

export const dashboardData = {
  kpis(ids: string[], tz = viewerTimeZone()) {
    return request<KpisResponse>(`/kpis${query({ ids: ids.join(","), tz })}`);
  },
  traffic(window: TrafficWindow, tz = viewerTimeZone()) {
    return request<TrafficResponse>(`/traffic${query({ window, tz })}`);
  },
  series(range: UsageRange, tz = viewerTimeZone()) {
    return request<TrafficSeriesResponse>(`/traffic/series${query({ range, granularity: "day", tz })}`);
  },
};

// ── Capacity and attention ──────────────────────────────────────────

/** A machine's reported state. Never derived from load. */
export type MachineHealth = "ok" | "degraded" | "error" | "maintenance" | "offline" | "stale";
export const MACHINE_HEALTH: MachineHealth[] = ["ok", "degraded", "error", "maintenance", "offline", "stale"];

export interface CapacityGpu {
  index: number;
  util_pct: number | null;
  vram_allocated_pct: number | null;
  temp_c: number | null;
}

export interface CapacityMachine {
  id: string;
  name: string;
  health: MachineHealth;
  last_seen: string | null;
  cpu_pct: number | null;
  gpu_util_pct: number | null;
  vram_allocated_pct: number | null;
  gpus: CapacityGpu[];
}

export interface CapacityGroup {
  kind: "cluster" | "unassigned";
  id: string | null;
  name: string | null;
  status: string | null;
  observed_at: string | null;
  stale: boolean;
  machines_total: number;
  machines_online: number;
  gpus_total: number | null;
  gpus_free: number | null;
  health_counts: Record<MachineHealth, number>;
  load_histogram: { gpu_util: number[]; vram_allocated: number[] };
  exceptions: { id: string; name: string; health: MachineHealth }[];
  machines: CapacityMachine[] | null;
  next_cursor: string | null;
}

export interface CapacityResponse {
  generated_at: string;
  totals: {
    clusters: number;
    clusters_healthy: number;
    machines: number;
    machines_online: number;
    gpus: number;
    gpus_free: number | null;
    gpus_free_unknown_clusters: number;
  };
  groups: CapacityGroup[];
  machines_included: boolean;
}

export type Severity = "critical" | "warning" | "info";

export interface AttentionItem {
  id: string;
  code: string;
  severity: Severity;
  category: string;
  params: Record<string, string | number | null>;
  since: string | null;
  link: string | null;
}

export interface AttentionResponse {
  generated_at: string;
  items: AttentionItem[];
  counts: Record<Severity, number>;
  rule_errors: string[];
}

export const dashboardFleet = {
  capacity(group?: string, cursor?: string) {
    const params: Record<string, string> = {};
    if (group) params.group = group;
    if (cursor) params.cursor = cursor;
    return request<CapacityResponse>(`/capacity${Object.keys(params).length ? query(params) : ""}`);
  },
  attention() {
    return request<AttentionResponse>("/attention");
  },
};
