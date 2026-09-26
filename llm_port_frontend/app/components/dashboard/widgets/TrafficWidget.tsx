/**
 * AI traffic: where requests were served in the chosen window -- the share by
 * residency class (our machines, our network, external, unknown), the top
 * destinations, and how fast and how reliably they answered.
 */
import { useTranslation } from "react-i18next";
import { Link as RouterLink } from "react-router";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import { PieChart } from "@mui/x-charts/PieChart";
import {
  RESIDENCY_CLASSES,
  type TrafficDestination,
  type TrafficResponse,
  type TrafficWindow,
} from "~/api/dashboard";
import { TOUCH_TARGET } from "../DashboardGrid";
import { fmtCompact, fmtMs, fmtShare } from "../format";
import { useTraffic } from "../queries";
import { RESIDENCY_LABEL_KEYS, residencyColor } from "../residency";
import type { WidgetProps } from "../types";
import WidgetCard, { WidgetEmpty } from "../WidgetCard";

const WINDOWS: TrafficWindow[] = ["today", "24h", "7d", "30d"];

export default function TrafficWidget({ layout, onLayoutChange, refreshToken }: WidgetProps) {
  const { t } = useTranslation();
  const window = layout.traffic_window;
  const query = useTraffic(window, { refreshToken });
  const data = query.data;

  return (
    <WidgetCard
      title={t("dashboard.widgets.traffic.title")}
      subtitle={t("dashboard.widgets.traffic.subtitle")}
      link={{ to: "/admin/observability", label: t("dashboard.widgets.traffic.link") }}
      actions={
        <ToggleButtonGroup
          size="small"
          exclusive
          value={window}
          onChange={(_, value: TrafficWindow | null) => value && onLayoutChange({ traffic_window: value })}
          aria-label={t("dashboard.window.label")}
        >
          {WINDOWS.map((w) => (
            <ToggleButton key={w} value={w} sx={{ px: 1, py: 0.25, textTransform: "none", ...TOUCH_TARGET }}>
              {t(`dashboard.window.${w}`)}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
      status={{
        loading: query.loading,
        error: query.error,
        hasData: Boolean(data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
      }}
      skeleton={
        <Stack direction={{ xs: "column", sm: "row" }} gap={2} alignItems="center">
          <Skeleton variant="circular" width={160} height={160} />
          <Stack gap={1} sx={{ flexGrow: 1, width: "100%" }}>
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} variant="rounded" height={22} />
            ))}
          </Stack>
        </Stack>
      }
      empty={
        data && data.requests === 0 ? (
          <WidgetEmpty
            message={t("dashboard.traffic.empty")}
            action={
              <Button component={RouterLink} to="/chat" size="small" sx={{ minHeight: 44 }}>
                {t("dashboard.traffic.empty_action")}
              </Button>
            }
          />
        ) : undefined
      }
    >
      {data && <TrafficBody data={data} />}
    </WidgetCard>
  );
}

function TrafficBody({ data }: { data: TrafficResponse }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const unrouted = data.requests - data.routed;

  return (
    <Stack gap={2}>
      <Box
        sx={{
          display: "grid",
          gap: 2.5,
          alignItems: "center",
          // Side by side when the widget is wide enough, stacked otherwise.
          gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        }}
      >
        <ResidencyDonut data={data} />
        <Destinations destinations={data.by_destination} routed={data.routed} />
      </Box>
      <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))" }}>
        <Mini label={t("dashboard.traffic.requests")} value={fmtCompact(data.requests, lang)} />
        <Mini label={t("dashboard.traffic.p50")} value={fmtMs(data.latency_ms.p50, lang)} />
        <Mini label={t("dashboard.traffic.p95")} value={fmtMs(data.latency_ms.p95, lang)} />
        <Mini label={t("dashboard.traffic.errors")} value={fmtShare(data.errors.rate, lang)} />
      </Box>
      {unrouted > 0 && (
        <Typography variant="caption" color="text.secondary">
          {t("dashboard.traffic.unrouted", { count: unrouted })}
        </Typography>
      )}
    </Stack>
  );
}

function ResidencyDonut({ data }: { data: TrafficResponse }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const byClass = new Map(data.by_residency.map((r) => [r.residency, r.requests]));
  const routed = data.routed;
  const inHouse = routed ? ((byClass.get("machines") ?? 0) + (byClass.get("private") ?? 0)) / routed : null;
  const slices = RESIDENCY_CLASSES.filter((c) => (byClass.get(c) ?? 0) > 0).map((c) => ({
    id: c,
    value: byClass.get(c) ?? 0,
    label: t(RESIDENCY_LABEL_KEYS[c]),
    color: residencyColor(theme, c),
  }));

  return (
    <Stack direction="row" gap={2} alignItems="center" justifyContent="center" sx={{ flexWrap: "wrap" }}>
      <Box
        sx={{ position: "relative", width: { xs: 128, sm: 160 }, height: { xs: 128, sm: 160 }, flexShrink: 0 }}
        role="img"
        aria-label={t("dashboard.traffic.donut_aria", { share: fmtShare(inHouse, i18n.language, 0) })}
      >
        {slices.length > 0 && (
          <PieChart
            series={[{ data: slices, innerRadius: "68%", outerRadius: "100%", paddingAngle: 1.5, cornerRadius: 3 }]}
            margin={{ top: 0, right: 0, bottom: 0, left: 0 }}
            hideLegend
            sx={{ width: "100%", height: "100%" }}
          />
        )}
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ position: "absolute", inset: 0, pointerEvents: "none" }}
          aria-hidden
        >
          <Typography sx={{ fontSize: { xs: 22, sm: 26 }, fontWeight: 700, lineHeight: 1 }}>
            {fmtShare(inHouse, i18n.language, 0)}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {t("dashboard.traffic.in_house")}
          </Typography>
        </Stack>
      </Box>
      <Stack gap={0.5} component="ul" sx={{ listStyle: "none", m: 0, p: 0 }}>
        {RESIDENCY_CLASSES.map((c) => (
          <Stack key={c} component="li" direction="row" alignItems="center" gap={1}>
            <Box aria-hidden sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: residencyColor(theme, c) }} />
            <Typography variant="caption" sx={{ minWidth: 92 }}>
              {t(RESIDENCY_LABEL_KEYS[c])}
            </Typography>
            <Typography variant="caption" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
              {fmtShare(routed ? (byClass.get(c) ?? 0) / routed : null, i18n.language, 0)}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
}

function Destinations({ destinations, routed }: { destinations: TrafficDestination[]; routed: number }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();

  function name(d: TrafficDestination): string {
    switch (d.kind) {
      case "this_server":
        return t("dashboard.traffic.this_server");
      case "other":
        return t("dashboard.traffic.other");
      case "unknown":
        return t("dashboard.traffic.unknown_destination");
      case "cluster":
        // A cluster since deleted keeps its requests but no longer its name.
        return d.name ?? t("dashboard.traffic.removed_cluster");
      default:
        return d.name ?? t("dashboard.traffic.unnamed");
    }
  }

  return (
    <Stack gap={1.25} component="ol" sx={{ listStyle: "none", m: 0, p: 0, minWidth: 0 }}>
      {destinations.map((d) => {
        const share = routed ? d.requests / routed : 0;
        const color = d.kind === "other" ? theme.palette.text.disabled : residencyColor(theme, d.residency);
        return (
          <Box
            key={`${d.kind}:${d.id ?? ""}`}
            component="li"
            sx={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 72px 44px", gap: 1, alignItems: "center" }}
          >
            <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
              <Box aria-hidden sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: color, flexShrink: 0 }} />
              <Typography variant="body2" noWrap sx={{ fontWeight: 600 }} title={name(d)}>
                {name(d)}
              </Typography>
            </Stack>
            <Box aria-hidden sx={{ height: 8, borderRadius: 999, bgcolor: "action.hover", overflow: "hidden" }}>
              <Box sx={{ width: `${Math.max(share * 100, 2)}%`, height: "100%", bgcolor: color, borderRadius: 999 }} />
            </Box>
            <Typography variant="caption" sx={{ textAlign: "right", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
              {fmtShare(share, i18n.language, 0)}
            </Typography>
          </Box>
        );
      })}
    </Stack>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ px: 1.25, py: 1, borderRadius: 1.5, border: 1, borderColor: "divider", bgcolor: "dashboard.surfaceSubtle" }}>
      <Typography sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{value}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase", letterSpacing: 0.4 }}>
        {label}
      </Typography>
    </Box>
  );
}
