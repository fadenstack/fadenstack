/**
 * PII Dashboard — aggregate statistics about PII processing.
 *
 * Shows total scans, detection rate, entity-type breakdown, daily volume,
 * and operation/source distribution.  **No raw PII data is displayed.**
 *
 * Built from the dashboard's kit (components/dashboard/kit): tiles, the
 * chart style and ranked bars, so it reads like the admin dashboard.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchPIIStats, type PIIStats } from "~/api/pii";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import { useTheme } from "@mui/material/styles";
import RefreshIcon from "@mui/icons-material/Refresh";

import {
  FULL_ROW,
  PageHeader,
  PanelGrid,
  RankedBars,
  StatGrid,
  StatTile,
  TimeSeriesChart,
  WidgetCard,
  WidgetEmpty,
  type RankedBar,
} from "~/components/dashboard/kit";
import { fmtMs } from "~/components/dashboard/format";

/** Counts by name as ranked bars, longest first, measured against the largest. */
function ranked(counts: Record<string, number>, lang: string, color?: string): RankedBar[] {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const top = entries[0]?.[1] ?? 0;
  const fmt = new Intl.NumberFormat(lang);
  return entries.map(([label, value]) => ({
    key: label,
    label,
    share: top ? value / top : 0,
    valueLabel: fmt.format(value),
    color,
  }));
}

export default function PIIDashboardPage() {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const lang = i18n.language;
  const [stats, setStats] = useState<PIIStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPIIStats()
      .then((d) => { if (!cancelled) setStats(d); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => load(), [load]);

  const num = new Intl.NumberFormat(lang);
  const pending = loading && !stats;
  const status = { loading: pending, hasData: Boolean(stats) };
  const day = new Intl.DateTimeFormat(lang, { day: "numeric", month: "short" });
  const detection = stats ? stats.detection_rate : null;
  const breakdowns: { key: string; title: string; counts: Record<string, number> | undefined; color: string }[] = [
    {
      key: "entity",
      title: t("pii_dashboard.entity_breakdown", "Entity Type Breakdown"),
      counts: stats?.entity_type_breakdown,
      color: theme.palette.dashboard.health.degraded,
    },
    {
      key: "operation",
      title: t("pii_dashboard.by_operation", "By Operation"),
      counts: stats?.operation_breakdown,
      color: theme.palette.dashboard.residency.machines,
    },
    {
      key: "source",
      title: t("pii_dashboard.by_source", "By Source"),
      counts: stats?.source_breakdown,
      color: theme.palette.dashboard.residency.private,
    },
  ];

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pb: 2 }}>
      <PageHeader
        title={t("pii_dashboard.title", "PII Dashboard")}
        subtitle={t("pii_dashboard.subtitle")}
        actions={
          <Tooltip title={t("common.refresh", "Refresh")}>
            <IconButton onClick={load} aria-label={t("common.refresh", "Refresh")}>
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        }
      />

      {error && <Alert severity="error">{error}</Alert>}
      {stats && stats.total_scans === 0 && (
        <Alert severity="info">
          {t("pii_dashboard.no_data", "No PII scan data yet. Events will appear as the gateway processes requests with PII policies enabled.")}
        </Alert>
      )}

      <PanelGrid columns={3}>
        <Box sx={FULL_ROW}>
          <WidgetCard
            title={t("pii_dashboard.summary")}
            status={status}
            skeleton={
              <StatGrid>
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <Skeleton key={i} variant="rounded" height={96} />
                ))}
              </StatGrid>
            }
          >
            {stats && (
              <StatGrid>
                <StatTile label={t("pii_dashboard.total_scans", "Total Scans")} value={num.format(stats.total_scans)} />
                <StatTile
                  label={t("pii_dashboard.with_pii", "With PII Detected")}
                  value={num.format(stats.total_with_pii)}
                  tone={stats.total_with_pii > 0 ? "warning" : "default"}
                />
                <StatTile label={t("pii_dashboard.total_entities", "Entities Found")} value={num.format(stats.total_entities)} />
                <StatTile
                  label={t("pii_dashboard.detection_rate", "Detection Rate")}
                  value={detection == null ? null : `${(detection * 100).toFixed(1)} %`}
                  tone={detection != null && detection > 0.5 ? "error" : "default"}
                />
                <StatTile
                  label={t("pii_dashboard.avg_duration", "Avg Processing Time")}
                  value={stats.avg_duration_ms == null ? null : fmtMs(stats.avg_duration_ms, lang)}
                  detail={
                    stats.p50_duration_ms != null
                      ? `${t("pii_dashboard.p50_short", "p50")} ${fmtMs(stats.p50_duration_ms, lang)}`
                      : undefined
                  }
                />
                <StatTile
                  label={t("pii_dashboard.p95_duration", "p95 Processing Time")}
                  value={stats.p95_duration_ms == null ? null : fmtMs(stats.p95_duration_ms, lang)}
                  detail={
                    stats.max_duration_ms != null
                      ? `${t("pii_dashboard.max_short", "max")} ${fmtMs(stats.max_duration_ms, lang)}`
                      : undefined
                  }
                  tone={stats.p95_duration_ms != null && stats.p95_duration_ms > 2000 ? "warning" : "default"}
                />
              </StatGrid>
            )}
          </WidgetCard>
        </Box>

        <Box sx={FULL_ROW}>
          <WidgetCard
            title={t("pii_dashboard.daily_volume", "Daily Scan Volume")}
            status={status}
            skeleton={<Skeleton variant="rounded" height={220} />}
            empty={stats && stats.daily_volume.length === 0 ? <WidgetEmpty message={t("pii_dashboard.no_volume")} /> : undefined}
          >
            {stats && (
              <TimeSeriesChart
                x={stats.daily_volume.map((d) => d.date)}
                xFormat={(v) => day.format(new Date(v))}
                series={[
                  {
                    id: "total",
                    label: t("pii_dashboard.total", "Total"),
                    data: stats.daily_volume.map((d) => d.count),
                    color: theme.palette.dashboard.residency.machines,
                  },
                  {
                    id: "pii",
                    label: t("pii_dashboard.pii_detected_label", "PII Detected"),
                    data: stats.daily_volume.map((d) => d.pii_count),
                    color: theme.palette.dashboard.health.degraded,
                  },
                ]}
                ariaLabel={t("pii_dashboard.daily_volume", "Daily Scan Volume")}
                height={220}
              />
            )}
          </WidgetCard>
        </Box>

        {breakdowns.map((b) => {
          const items = b.counts ? ranked(b.counts, lang, b.color) : [];
          return (
            <WidgetCard
              key={b.key}
              title={b.title}
              status={status}
              empty={stats && items.length === 0 ? <WidgetEmpty message={t("pii_dashboard.no_breakdown")} /> : undefined}
            >
              <RankedBars items={items} barWidth={80} />
            </WidgetCard>
          );
        })}
      </PanelGrid>
    </Box>
  );
}
