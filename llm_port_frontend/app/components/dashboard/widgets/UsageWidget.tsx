/**
 * Usage: routed requests per local calendar day, stacked by residency class,
 * over the last 7 or 30 days. Today's bucket is still filling.
 */
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { LineChart } from "@mui/x-charts/LineChart";
import { RESIDENCY_CLASSES, type UsageRange } from "~/api/dashboard";
import { TOUCH_TARGET } from "../DashboardGrid";
import { fmtInt, fmtShare } from "../format";
import { useTrafficSeries } from "../queries";
import { RESIDENCY_LABEL_KEYS, residencyColor } from "../residency";
import type { WidgetProps } from "../types";
import WidgetCard, { WidgetEmpty } from "../WidgetCard";

const RANGES: UsageRange[] = ["7d", "30d"];

/** "2026-09-26" as a local date (not UTC midnight, which is the day before west of Greenwich). */
function localDate(label: string): Date {
  const [y, m, d] = label.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export default function UsageWidget({ layout, onLayoutChange, refreshToken }: WidgetProps) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const range = layout.usage_range;
  const query = useTrafficSeries(range, { refreshToken });
  const buckets = query.data?.buckets ?? [];
  const total = buckets.reduce((sum, b) => sum + RESIDENCY_CLASSES.reduce((s, c) => s + b[c], 0), 0);
  const inHouse = buckets.reduce((sum, b) => sum + b.machines + b.private, 0);
  const dayFormat = new Intl.DateTimeFormat(
    i18n.language,
    range === "7d" ? { weekday: "short" } : { day: "numeric", month: "short" },
  );

  return (
    <WidgetCard
      title={t("dashboard.widgets.usage.title")}
      subtitle={t(`dashboard.widgets.usage.subtitle_${range}`)}
      link={{ to: "/admin/observability", label: t("dashboard.widgets.usage.link") }}
      actions={
        <ToggleButtonGroup
          size="small"
          exclusive
          value={range}
          onChange={(_, value: UsageRange | null) => value && onLayoutChange({ usage_range: value })}
          aria-label={t("dashboard.range.label")}
        >
          {RANGES.map((r) => (
            <ToggleButton key={r} value={r} sx={{ px: 1, py: 0.25, textTransform: "none", ...TOUCH_TARGET }}>
              {t(`dashboard.range.${r}`)}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
      status={{
        loading: query.loading,
        error: query.error,
        hasData: Boolean(query.data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
      }}
      skeleton={<Skeleton variant="rounded" height={isPhone ? 160 : 190} />}
      empty={query.data && total === 0 ? <WidgetEmpty message={t("dashboard.usage.empty")} /> : undefined}
    >
      <Stack direction="row" gap={1.5} sx={{ flexWrap: "wrap", mb: 0.5 }} component="ul" aria-hidden>
        {RESIDENCY_CLASSES.map((c) => (
          <Stack key={c} component="li" direction="row" alignItems="center" gap={0.75} sx={{ listStyle: "none" }}>
            <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: residencyColor(theme, c) }} />
            <Typography variant="caption">{t(RESIDENCY_LABEL_KEYS[c])}</Typography>
          </Stack>
        ))}
      </Stack>
      <Box
        role="img"
        aria-label={t("dashboard.usage.chart_aria", {
          total: fmtInt(total, i18n.language),
          share: fmtShare(total ? inHouse / total : null, i18n.language, 0),
        })}
      >
        <LineChart
          height={isPhone ? 160 : 190}
          margin={{ left: 8, right: 24, top: 8, bottom: 4 }}
          hideLegend
          skipAnimation
          xAxis={[
            {
              scaleType: "point",
              data: buckets.map((b) => b.bucket),
              valueFormatter: (label: string) => dayFormat.format(localDate(label)),
              // Every other day on a phone; the axis would crowd otherwise.
              tickInterval: (_value: string, index: number) => !isPhone || index % 2 === 0,
            },
          ]}
          yAxis={[{ width: 40, valueFormatter: (v: number) => new Intl.NumberFormat(i18n.language, { notation: "compact" }).format(v) }]}
          series={RESIDENCY_CLASSES.map((c) => ({
            id: c,
            label: t(RESIDENCY_LABEL_KEYS[c]),
            data: buckets.map((b) => b[c]),
            color: residencyColor(theme, c),
            area: true,
            stack: "total",
            showMark: false,
            curve: "monotoneX" as const,
          }))}
        />
      </Box>
      <Typography variant="caption" color="text.secondary">
        {t("dashboard.usage.total", {
          requests: fmtInt(total, i18n.language),
          share: fmtShare(total ? inHouse / total : null, i18n.language, 0),
        })}
      </Typography>
    </WidgetCard>
  );
}
