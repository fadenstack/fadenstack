/**
 * The dashboard's shared queries: one key and one fetcher per endpoint, so
 * every widget that reads, say, the overview shares one request.
 */
import { dashboard, hardware } from "~/api/admin";
import { dashboardData, dashboardFleet, viewerTimeZone, type TrafficWindow, type UsageRange } from "~/api/dashboard";
import { nodesApi } from "~/api/nodes";
import { useDashboardQuery } from "~/lib/dashboardQuery";

interface Opts {
  refreshToken: number;
  enabled?: boolean;
}

/** How often the overview refreshes; the page's refresh ring counts down to it. */
export const OVERVIEW_REFRESH_MS = 30_000;

export function useOverview({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/dashboard/overview", () => dashboard.overview(), {
    refreshMs: OVERVIEW_REFRESH_MS,
    refreshToken,
    enabled,
  });
}

export function useDependencyHealth({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/dashboard/health", () => dashboard.health(), {
    refreshMs: 30_000,
    refreshToken,
    enabled,
  });
}

export function useTopContainers({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/dashboard/top-containers", () => dashboard.topContainers(), {
    refreshMs: 30_000,
    refreshToken,
    enabled,
  });
}

export function useHardware({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/hardware", () => hardware.info(), {
    refreshMs: 300_000,
    refreshToken,
    enabled,
  });
}

export function useNodes({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/system/nodes", () => nodesApi.list(), {
    refreshMs: 30_000,
    refreshToken,
    enabled,
  });
}

export function useKpis(ids: string[], { refreshToken, enabled }: Opts) {
  const tz = viewerTimeZone();
  return useDashboardQuery(`admin/dashboard/kpis?ids=${ids.join(",")}&tz=${tz}`, () => dashboardData.kpis(ids, tz), {
    refreshMs: 60_000,
    refreshToken,
    enabled: enabled !== false && ids.length > 0,
  });
}

export function useTraffic(window: TrafficWindow, { refreshToken, enabled }: Opts) {
  const tz = viewerTimeZone();
  return useDashboardQuery(`admin/dashboard/traffic?window=${window}&tz=${tz}`, () => dashboardData.traffic(window, tz), {
    refreshMs: 60_000,
    refreshToken,
    enabled,
  });
}

export function useTrafficSeries(range: UsageRange, { refreshToken, enabled }: Opts) {
  const tz = viewerTimeZone();
  return useDashboardQuery(`admin/dashboard/traffic/series?range=${range}&tz=${tz}`, () => dashboardData.series(range, tz), {
    refreshMs: 300_000,
    refreshToken,
    enabled,
  });
}

export function useCapacity({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/dashboard/capacity", () => dashboardFleet.capacity(), {
    refreshMs: 30_000,
    refreshToken,
    enabled,
  });
}

export function useAttention({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/dashboard/attention", () => dashboardFleet.attention(), {
    refreshMs: 30_000,
    refreshToken,
    enabled,
  });
}
