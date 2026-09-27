/**
 * Usage: routed requests per local calendar day, stacked by residency class,
 * over the last 7 or 30 days. Today's bucket is still filling.
 */
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { RESIDENCY_CLASSES, type UsageRange } from "~/api/dashboard";
import { fmtInt, fmtShare } from "../format";
import { useTrafficSeries } from "../queries";
import { RESIDENCY_LABEL_KEYS, residencyColor } from "../residency";
import type { WidgetProps } from "../types";
import RangeToggle from "../RangeToggle";
import TimeSeriesChart from "../TimeSeriesChart";
import WidgetCard, { WidgetEmpty } from "../WidgetCard";

const RANGES: UsageRange[] = ["7d", "30d"];

/** "2026-09-26" as a local date (not UTC midnight, which is the day before west of Greenwich). */
function localDate(label: string): Date {
  const [y, m, d] = label.split("-").map(Number);
  return new Date(y, m - 1, d);
}

const CHART_HEIGHT = { phone: 160, other: 190 };

export default function UsageWidget({ layout, onLayoutChange, refreshToken, fill = false }: WidgetProps) {
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
        <RangeToggle
          value={range}
          options={RANGES.map((r) => ({ value: r, label: t(`dashboard.range.${r}`) }))}
          onChange={(value: UsageRange) => onLayoutChange({ usage_range: value })}
          ariaLabel={t("dashboard.range.label")}
        />
      }
      status={{
        loading: query.loading,
        error: query.error,
        hasData: Boolean(query.data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
      }}
      skeleton={<Skeleton variant="rounded" height={isPhone ? CHART_HEIGHT.phone : CHART_HEIGHT.other} />}
      empty={query.data && total === 0 ? <WidgetEmpty message={t("dashboard.usage.empty")} /> : undefined}
    >
      <Box sx={fill ? { height: "100%", display: "flex", flexDirection: "column", minHeight: 0 } : undefined}>
      <TimeSeriesChart
        x={buckets.map((b) => b.bucket)}
        xFormat={(label) => dayFormat.format(localDate(String(label)))}
        series={RESIDENCY_CLASSES.map((c) => ({
          id: c,
          label: t(RESIDENCY_LABEL_KEYS[c]),
          data: buckets.map((b) => b[c]),
          color: residencyColor(theme, c),
        }))}
        stacked
        legend
        fill={fill}
        ariaLabel={t("dashboard.usage.chart_aria", {
          total: fmtInt(total, i18n.language),
          share: fmtShare(total ? inHouse / total : null, i18n.language, 0),
        })}
      />
      <Typography variant="caption" color="text.secondary">
        {t("dashboard.usage.total", {
          requests: fmtInt(total, i18n.language),
          share: fmtShare(total ? inHouse / total : null, i18n.language, 0),
        })}
      </Typography>
      </Box>
    </WidgetCard>
  );
}
