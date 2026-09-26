/**
 * The dashboard's shared queries: one key and one fetcher per endpoint, so
 * every widget that reads, say, the overview shares one request.
 */
import { dashboard, hardware } from "~/api/admin";
import { nodesApi } from "~/api/nodes";
import { useDashboardQuery } from "~/lib/dashboardQuery";

interface Opts {
  refreshToken: number;
  enabled?: boolean;
}

export function useOverview({ refreshToken, enabled }: Opts) {
  return useDashboardQuery("admin/dashboard/overview", () => dashboard.overview(), {
    refreshMs: 30_000,
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
