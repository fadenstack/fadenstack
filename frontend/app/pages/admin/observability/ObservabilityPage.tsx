/**
 * Observability Dashboard — cost, usage, and performance overview.
 *
 * Tabs: Overview (charts + summary), Requests (paginated log), Pricing (editor).
 * The overview is built from the dashboard's kit (components/dashboard/kit).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  observability,
  type Summary,
  type TimeseriesBucket,
  type Performance,
  type PaginatedRequests,
  type PricingEntry,
} from "~/api/observability";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import RefreshIcon from "@mui/icons-material/Refresh";
import DownloadIcon from "@mui/icons-material/Download";
import {
  FULL_ROW,
  PageHeader,
  PanelGrid,
  RangeToggle,
  RankedBars,
  StatGrid,
  StatTile,
  TimeSeriesChart,
  WidgetCard,
  WidgetEmpty,
} from "~/components/dashboard/kit";

import RequestsTab from "./RequestsTab";
import PricingTab from "./PricingTab";

// ── Helpers ──────────────────────────────────────────────────────────────────

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

function fmtCost(v: number | null | undefined): string {
  if (v == null) return "$0.00";
  return `$${Number(v).toFixed(4)}`;
}

function fmtNum(v: number | null | undefined): string {
  if (v == null) return "0";
  return v.toLocaleString();
}

function fmtMs(v: number | null | undefined): string {
  if (v == null) return "N/A";
  return `${Math.round(v)} ms`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "N/A";
  return `${(v * 100).toFixed(2)}%`;
}

type RangeKey = "7d" | "14d" | "30d" | "90d";
const RANGES: Record<RangeKey, number> = {
  "7d": 7,
  "14d": 14,
  "30d": 30,
  "90d": 90,
};

// ── Component ────────────────────────────────────────────────────────────────

export default function ObservabilityPage() {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const [tab, setTab] = useState(0);
  const [range, setRange] = useState<RangeKey>("7d");
  // One flag per panel, not one for the page.
  //
  // These three queries scan different amounts of history -- the performance
  // percentiles are much the heaviest -- and gathering them in a `Promise.all`
  // meant the summary cards, which return almost immediately, sat as
  // skeletons until the slowest had finished. Every panel here already draws
  // its own skeleton; they just all watched the same flag.
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [seriesLoading, setSeriesLoading] = useState(true);
  const [perfLoading, setPerfLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [summary, setSummary] = useState<Summary | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesBucket[]>([]);
  const [perf, setPerf] = useState<Performance | null>(null);

  const start = useMemo(() => daysAgo(RANGES[range]), [range]);
  const end = useMemo(() => new Date().toISOString(), []);

  const loadOverview = useCallback(async () => {
    setError(null);
    // The error banner reports the first failure; a panel that failed on its
    // own still shows its own empty state rather than pretending to a zero.
    const fail = (err: unknown) =>
      setError((prev) =>
        prev ?? (err instanceof Error ? err.message : "Failed to load data"),
      );

    setSummaryLoading(true);
    setSeriesLoading(true);
    setPerfLoading(true);

    void observability
      .summary(start, end)
      .then(setSummary)
      .catch(fail)
      .finally(() => setSummaryLoading(false));

    void observability
      .timeseries(start, end, RANGES[range] <= 7 ? "hour" : "day")
      .then(setTimeseries)
      .catch(fail)
      .finally(() => setSeriesLoading(false));

    void observability
      .performance(start, end)
      .then(setPerf)
      .catch(fail)
      .finally(() => setPerfLoading(false));
  }, [start, end, range]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  // ── Overview tab ─────────────────────────────────────────────────
  // Built from the dashboard's kit, so it reads like the dashboard: a summary
  // of tiles, charts in the same style, rankings as bars.

  const lang = i18n.language;
  const hourly = RANGES[range] <= 7;
  const bucketFormat = new Intl.DateTimeFormat(
    lang,
    hourly ? { weekday: "short", hour: "2-digit" } : { day: "numeric", month: "short" },
  );
  const usdCompact = new Intl.NumberFormat(lang, {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumSignificantDigits: 2,
  });
  const errorRate = perf?.error_rate ?? null;
  const errorTone = errorRate == null ? "default" : errorRate > 0.05 ? "error" : errorRate > 0.01 ? "warning" : "default";
  const models = (summary?.by_model ?? []).slice(0, 10);
  const topModelCost = Math.max(0, ...models.map((m) => Number(m.estimated_total_cost) || 0));
  // No ranking of people here (M11): usage per team, agent or model is on the Usage page, k-suppressed.
  const seriesStatus = { loading: seriesLoading, hasData: timeseries.length > 0 };
  const noSeries = !seriesLoading && timeseries.length === 0
    ? <WidgetEmpty message={t("observability.no_data")} />
    : undefined;

  const overviewContent = (
    <PanelGrid>
      <Box sx={FULL_ROW}>
        <WidgetCard
          title={t("observability.summary")}
          status={{ loading: summaryLoading && !summary, hasData: Boolean(summary) }}
          skeleton={
            <StatGrid>
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} variant="rounded" height={96} />
              ))}
            </StatGrid>
          }
        >
          <StatGrid>
            <StatTile label={t("observability.estimated_spend")} value={fmtCost(summary?.estimated_total_cost)} />
            <StatTile label={t("observability.total_requests")} value={fmtNum(summary?.total_requests)} />
            <StatTile
              label={t("observability.total_tokens")}
              value={fmtNum(summary?.total_tokens)}
              detail={
                summary
                  ? t("observability.tokens_split", {
                      prompt: fmtNum(summary.total_prompt_tokens),
                      completion: fmtNum(summary.total_completion_tokens),
                    })
                  : undefined
              }
            />
            <StatTile label={t("observability.avg_latency")} value={perf ? fmtMs(perf.avg_latency_ms) : null} loading={perfLoading} />
            <StatTile label={t("observability.error_rate")} value={perf ? fmtPct(errorRate) : null} tone={errorTone} loading={perfLoading} />
          </StatGrid>
        </WidgetCard>
      </Box>

      <WidgetCard title={t("observability.cost_over_time")} status={seriesStatus} empty={noSeries}>
        <TimeSeriesChart
          x={timeseries.map((b) => new Date(b.bucket))}
          xFormat={(v) => bucketFormat.format(new Date(v))}
          series={[
            {
              id: "cost",
              label: t("observability.chart_cost"),
              data: timeseries.map((b) => Number(b.estimated_total_cost) || 0),
              color: theme.palette.primary.main,
            },
          ]}
          yFormat={(v) => usdCompact.format(v)}
          yWidth={56}
          ariaLabel={t("observability.cost_over_time")}
          height={220}
        />
      </WidgetCard>

      <WidgetCard title={t("observability.request_throughput")} status={seriesStatus} empty={noSeries}>
        <TimeSeriesChart
          x={timeseries.map((b) => new Date(b.bucket))}
          xFormat={(v) => bucketFormat.format(new Date(v))}
          series={[
            {
              id: "requests",
              label: t("observability.col_requests"),
              data: timeseries.map((b) => b.total_requests),
              color: theme.palette.dashboard.residency.machines,
            },
            {
              id: "errors",
              label: t("observability.error"),
              data: timeseries.map((b) => b.error_count),
              color: theme.palette.dashboard.health.error,
              area: false,
            },
          ]}
          ariaLabel={t("observability.request_throughput")}
          height={220}
        />
      </WidgetCard>

      <WidgetCard
        title={t("observability.latency_percentiles")}
        status={{ loading: perfLoading && !perf, hasData: Boolean(perf) }}
      >
        <Stack gap={1.5}>
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.75 }}>
              {t("observability.latency_total")}
            </Typography>
            <StatGrid min={90} gap={1}>
              <StatTile variant="compact" label="p50" value={perf ? fmtMs(perf.p50_latency_ms) : null} />
              <StatTile variant="compact" label="p95" value={perf ? fmtMs(perf.p95_latency_ms) : null} />
              <StatTile variant="compact" label="p99" value={perf ? fmtMs(perf.p99_latency_ms) : null} />
            </StatGrid>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.75 }}>
              {t("observability.latency_ttft")}
            </Typography>
            <StatGrid min={90} gap={1}>
              <StatTile variant="compact" label="p50" value={perf ? fmtMs(perf.p50_ttft_ms) : null} />
              <StatTile variant="compact" label="p95" value={perf ? fmtMs(perf.p95_ttft_ms) : null} />
              <StatTile variant="compact" label="p99" value={perf ? fmtMs(perf.p99_ttft_ms) : null} />
            </StatGrid>
          </Box>
        </Stack>
      </WidgetCard>

      <WidgetCard
        title={t("observability.spend_by_model")}
        status={{ loading: summaryLoading && !summary, hasData: Boolean(summary) }}
        empty={summary && models.length === 0 ? <WidgetEmpty message={t("observability.no_data_short")} /> : undefined}
      >
        <RankedBars
          items={models.map((m) => {
            const cost = Number(m.estimated_total_cost) || 0;
            return {
              key: m.model_alias,
              label: m.model_alias,
              share: topModelCost ? cost / topModelCost : 0,
              valueLabel: fmtCost(cost),
            };
          })}
          barWidth={96}
        />
      </WidgetCard>
    </PanelGrid>
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pb: 2 }}>
      <PageHeader
        title={t("observability.title")}
        subtitle={t("observability.subtitle")}
        actions={
          <>
            <RangeToggle
              value={range}
              options={(Object.keys(RANGES) as RangeKey[]).map((k) => ({ value: k, label: k }))}
              onChange={setRange}
              ariaLabel={t("observability.range_label")}
            />
            <Tooltip title={t("observability.refresh")}>
              <IconButton onClick={loadOverview} aria-label={t("observability.refresh")}>
                <RefreshIcon />
              </IconButton>
            </Tooltip>
            <Tooltip title={t("observability.export_csv")}>
              <IconButton
                component="a"
                href={observability.exportCsvUrl(start, end)}
                target="_blank"
                aria-label={t("observability.export_csv")}
              >
                <DownloadIcon />
              </IconButton>
            </Tooltip>
          </>
        }
      />

      {error && <Alert severity="error">{error}</Alert>}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" scrollButtons={false}>
        <Tab label={t("observability.tab_overview")} />
        <Tab label={t("observability.tab_requests")} />
        <Tab label={t("observability.tab_pricing")} />
      </Tabs>

      {tab === 0 && overviewContent}
      {tab === 1 && <RequestsTab start={start} end={end} />}
      {tab === 2 && <PricingTab />}
    </Box>
  );
}
