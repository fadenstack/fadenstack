/**
 * Platform health: overall state, dependencies, modules and the database, in
 * one row of cells. Each cell renders as soon as its own source answers.
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import { useServices } from "~/lib/ServicesContext";
import { fmtInt, fmtMs, fmtShare } from "../format";
import { useDependencyHealth, useKpis, useOverview } from "../queries";
import type { WidgetProps } from "../types";
import WidgetCard from "../WidgetCard";

type Tone = "ok" | "degraded" | "error" | "offline";

export default function PlatformHealthWidget({ refreshToken }: WidgetProps) {
  const { t, i18n } = useTranslation();
  const overview = useOverview({ refreshToken });
  const deps = useDependencyHealth({ refreshToken });
  const errors = useKpis(["error_rate_1h"], { refreshToken });
  const errorKpi = errors.data?.items.find((i) => i.id === "error_rate_1h");
  const errorRate = typeof errorKpi?.value === "number" ? errorKpi.value : null;
  const { services, loading: servicesLoading } = useServices();

  const o = overview.data;
  const down = deps.data?.items.filter((i) => i.status !== "up") ?? [];
  const enabledModules = services.filter((s) => s.enabled);
  const healthyModules = enabledModules.filter((s) => s.status === "healthy");

  const overallTone: Tone = !o ? "offline" : o.system_badge === "red" ? "error" : o.system_badge === "yellow" ? "degraded" : "ok";
  const pgPct =
    o?.postgres_connections != null && o.postgres_max_connections
      ? o.postgres_connections / o.postgres_max_connections
      : null;

  return (
    <WidgetCard
      title={t("dashboard.widgets.platform_health.title")}
      status={{
        loading: overview.loading && deps.loading,
        error: overview.error && deps.error ? overview.error : null,
        hasData: Boolean(o || deps.data),
        onRetry: () => {
          void overview.refresh();
          void deps.refresh();
        },
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <Cell title={t("dashboard.health.overall")} loading={overview.loading}>
          {o && (
            <Status tone={overallTone} label={t(`dashboard.health.system.${o.system_status}`, { defaultValue: o.system_status })} />
          )}
        </Cell>
        <Cell title={t("dashboard.health.dependencies")} loading={deps.loading}>
          {deps.data &&
            (down.length === 0 ? (
              <Status tone="ok" label={t("dashboard.health.all_up", { count: deps.data.items.length })} />
            ) : (
              <Tooltip title={down.map((d) => `${d.name}: ${d.status}${d.detail ? ` (${d.detail})` : ""}`).join("\n")}>
                <span>
                  <Status tone="error" label={t("dashboard.health.some_down", { names: down.map((d) => d.name).join(", ") })} />
                </span>
              </Tooltip>
            ))}
        </Cell>
        <Cell title={t("dashboard.health.modules")} loading={servicesLoading}>
          {!servicesLoading && (
            <Status
              tone={healthyModules.length === enabledModules.length ? "ok" : "degraded"}
              label={t("dashboard.health.modules_healthy", {
                healthy: healthyModules.length,
                enabled: enabledModules.length,
              })}
            />
          )}
        </Cell>
        <Cell title={t("dashboard.health.gateway_errors")} loading={errors.loading}>
          {errors.data &&
            (errorKpi ? (
              <Status
                tone={errorRate != null && errorRate >= 0.05 ? "error" : errorRate != null && errorRate >= 0.01 ? "degraded" : "ok"}
                label={
                  errorKpi.context.requests
                    ? t("dashboard.health.error_rate", {
                        rate: fmtShare(errorRate, i18n.language),
                        p95: fmtMs(errorKpi.context.p95_ms, i18n.language),
                      })
                    : t("dashboard.health.no_requests")
                }
              />
            ) : (
              <Typography variant="body2" color="text.secondary">
                {t("dashboard.kpi.not_available")}
              </Typography>
            ))}
        </Cell>
        <Cell title={t("dashboard.health.postgres")} loading={overview.loading}>
          {o &&
            (o.postgres_connections == null ? (
              <Status tone="error" label={t("dashboard.health.unreachable")} />
            ) : (
              <Typography variant="body2" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                {fmtInt(o.postgres_connections, i18n.language)}
                <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
                  {o.postgres_max_connections
                    ? t("dashboard.health.of_connections", { max: fmtInt(o.postgres_max_connections, i18n.language) })
                    : t("dashboard.health.connections")}
                </Typography>
                {pgPct != null && pgPct > 0.8 && (
                  <Box component="span" sx={{ ml: 0.75 }}>
                    <Status tone="degraded" label={t("dashboard.health.near_limit")} inline />
                  </Box>
                )}
              </Typography>
            ))}
        </Cell>
      </Box>
    </WidgetCard>
  );
}

function Cell({ title, loading, children }: { title: string; loading?: boolean; children?: ReactNode }) {
  return (
    <Box
      sx={{
        px: 1.75,
        py: 1.25,
        borderRight: 1,
        borderBottom: 1,
        borderColor: "divider",
        // The grid's outer border already draws the last edges.
        mr: "-1px",
        mb: "-1px",
        minWidth: 0,
      }}
    >
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
        {title}
      </Typography>
      {loading ? <Skeleton variant="text" width="70%" /> : children}
    </Box>
  );
}

/** A dot and a label: the colour repeats the word, never replaces it. */
function Status({ tone, label, inline }: { tone: Tone; label: string; inline?: boolean }) {
  const theme = useTheme();
  const color = theme.palette.dashboard.health[tone];
  return (
    <Stack direction="row" alignItems="center" gap={0.75} component={inline ? "span" : "div"} sx={{ display: inline ? "inline-flex" : "flex" }}>
      <Box component="span" aria-hidden sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: color, flexShrink: 0 }} />
      <Typography component="span" variant="body2" sx={{ fontWeight: 700 }}>
        {label}
      </Typography>
    </Stack>
  );
}
