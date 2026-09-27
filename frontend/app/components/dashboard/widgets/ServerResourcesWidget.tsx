/**
 * Server resources: CPU, RAM, disk and GPU of the machine the backend runs on.
 * On a single-machine install this is the most useful widget there is.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import RefreshIcon from "@mui/icons-material/Refresh";
import { hardware } from "~/api/admin";
import GaugeCard from "~/components/GaugeCard";
import { fmtBytes } from "../format";
import { useHardware, useOverview } from "../queries";
import { TOUCH_TARGET } from "../DashboardGrid";
import type { WidgetProps } from "../types";
import WidgetCard from "../WidgetCard";

const GAUGE_GRID = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
  gap: 1.5,
} as const;

function ratio(used: number | null | undefined, total: number | null | undefined, lang: string): string | undefined {
  if (used == null || total == null || total <= 0) return undefined;
  return `${fmtBytes(used, lang)} / ${fmtBytes(total, lang)}`;
}

export default function ServerResourcesWidget({ refreshToken }: WidgetProps) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const overview = useOverview({ refreshToken });
  const hw = useHardware({ refreshToken });
  const [rescanning, setRescanning] = useState(false);
  const o = overview.data;
  const h = hw.data;

  const ramPct = o?.ram_used_bytes && o.ram_total_bytes ? (o.ram_used_bytes / o.ram_total_bytes) * 100 : null;
  const diskPct = o?.disk_free_percent == null ? null : 100 - o.disk_free_percent;
  const vramPct =
    o?.gpu_vram_used_bytes && o.gpu_vram_total_bytes ? (o.gpu_vram_used_bytes / o.gpu_vram_total_bytes) * 100 : null;
  const hasGpu = Boolean(h?.gpu.has_gpu) || o?.gpu_util_percent != null || vramPct != null;

  async function rescan() {
    setRescanning(true);
    try {
      await hardware.rescan();
      await hw.refresh();
    } finally {
      setRescanning(false);
    }
  }

  return (
    <WidgetCard
      title={t("dashboard.widgets.server_resources.title")}
      subtitle={t("dashboard.widgets.server_resources.subtitle")}
      status={{
        loading: overview.loading,
        error: overview.error,
        hasData: Boolean(o),
        onRetry: overview.refresh,
        stale: overview.stale,
        updatedAt: overview.updatedAt,
      }}
      skeleton={
        <Box sx={GAUGE_GRID}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} variant="rounded" height={170} />
          ))}
        </Box>
      }
    >
      {o && (
        <Box sx={GAUGE_GRID}>
          <GaugeCard
            label={t("dashboard.gauges.cpu")}
            value={o.cpu_percent ?? null}
            // Windows has no load average; say nothing rather than "- / - / -".
            detail={
              o.load_1m == null
                ? undefined
                : t("dashboard.gauges.load", {
                    one: o.load_1m.toFixed(2),
                    five: o.load_5m?.toFixed(2) ?? "-",
                    fifteen: o.load_15m?.toFixed(2) ?? "-",
                  })
            }
          />
          <GaugeCard
            label={t("dashboard.gauges.ram")}
            value={ramPct}
            detail={ratio(o.ram_used_bytes, o.ram_total_bytes, lang)}
            secondaryDetail={t("dashboard.gauges.swap", {
              value: ratio(o.swap_used_bytes, o.swap_total_bytes, lang) ?? "–",
            })}
          />
          <GaugeCard
            label={t("dashboard.gauges.disk")}
            value={diskPct}
            detail={t("dashboard.gauges.disk_free_of", {
              free: fmtBytes(o.disk_free_bytes, lang),
              total: fmtBytes(o.disk_total_bytes, lang),
            })}
          />
          {hasGpu && (
            <Box sx={{ position: "relative" }}>
              <GaugeCard
                label={h?.gpu.has_gpu ? `GPU (${h.gpu.primary_vendor.toUpperCase()})` : t("dashboard.gauges.gpu")}
                value={o.gpu_util_percent ?? vramPct}
                innerText={o.gpu_util_percent == null && vramPct != null ? `${vramPct.toFixed(1)}%` : undefined}
                detail={ratio(o.gpu_vram_used_bytes, o.gpu_vram_total_bytes, lang)}
                secondaryDetail={
                  h?.gpu.has_gpu
                    ? `${h.gpu.devices.find((d) => d.vendor === h.gpu.primary_vendor)?.model ?? h.gpu.devices[0]?.model ?? ""} · ${h.gpu.primary_compute_api.toUpperCase()}`
                    : t("dashboard.gauges.vram")
                }
              />
              <Tooltip title={t("dashboard.gpu_rescan")}>
                <IconButton
                  size="small"
                  onClick={rescan}
                  disabled={rescanning}
                  aria-label={t("dashboard.gpu_rescan")}
                  sx={{ position: "absolute", top: 4, right: 4, ...TOUCH_TARGET }}
                >
                  {rescanning ? <CircularProgress size={16} /> : <RefreshIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
              {h?.gpu.has_gpu && h.gpu.device_count > 1 && (
                <Tooltip
                  title={h.gpu.devices
                    .map((d) => `#${d.index} ${d.vendor.toUpperCase()} ${d.model} (${fmtBytes(d.vram_bytes, lang)})`)
                    .join("\n")}
                >
                  <Chip
                    label={t("dashboard.gpu_count", { count: h.gpu.device_count })}
                    size="small"
                    variant="outlined"
                    sx={{ position: "absolute", bottom: 6, right: 6 }}
                  />
                </Tooltip>
              )}
            </Box>
          )}
        </Box>
      )}
    </WidgetCard>
  );
}
