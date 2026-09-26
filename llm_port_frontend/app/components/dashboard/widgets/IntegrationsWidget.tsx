/**
 * Integrations: the LLM providers, MCP servers and skills the platform is
 * connected to. A tile per module that is on (the server leaves a module that
 * is off out); each opens its management page.
 */
import type { ReactNode } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import CardActionArea from "@mui/material/CardActionArea";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh";
import CloudQueueIcon from "@mui/icons-material/CloudQueue";
import HubIcon from "@mui/icons-material/Hub";
import { RESIDENCY_CLASSES, type IntegrationsResponse } from "~/api/dashboard";
import { fmtInt } from "../format";
import { useIntegrations } from "../queries";
import { RESIDENCY_LABEL_KEYS, residencyColor } from "../residency";
import type { WidgetProps } from "../types";
import WidgetCard from "../WidgetCard";

export default function IntegrationsWidget({ refreshToken }: WidgetProps) {
  const { t } = useTranslation();
  const query = useIntegrations({ refreshToken });
  const data = query.data;

  return (
    <WidgetCard
      title={t("dashboard.widgets.integrations.title")}
      subtitle={t("dashboard.widgets.integrations.subtitle")}
      status={{
        loading: query.loading,
        error: query.error,
        hasData: Boolean(data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
        partial: data?.unavailable.length
          ? data.unavailable.map((m) => t("dashboard.integrations.unavailable", { module: t(`dashboard.integrations.${m}`) }))
          : null,
      }}
      skeleton={
        <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 1 }}>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} variant="rounded" height={76} />
          ))}
        </Box>
      }
    >
      {data && <Tiles data={data} />}
    </WidgetCard>
  );
}

function Tiles({ data }: { data: IntegrationsResponse }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const lang = i18n.language;
  const providers = data.providers;

  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 1 }}>
      <Tile
        to="/admin/llm/providers"
        icon={<CloudQueueIcon fontSize="small" />}
        label={t("dashboard.integrations.providers")}
        value={fmtInt(providers.total, lang)}
        warning={providers.unreachable ? t("dashboard.integrations.unreachable", { count: providers.unreachable }) : undefined}
      >
        {providers.total > 0 && (
          <Stack direction="row" gap={1} sx={{ flexWrap: "wrap" }}>
            {RESIDENCY_CLASSES.filter((c) => providers.by_residency[c]).map((c) => (
              <Stack key={c} direction="row" alignItems="center" gap={0.5}>
                <Box aria-hidden sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: residencyColor(theme, c) }} />
                <Typography variant="caption" color="text.secondary">
                  {t(RESIDENCY_LABEL_KEYS[c])} {providers.by_residency[c]}
                </Typography>
              </Stack>
            ))}
          </Stack>
        )}
      </Tile>
      {data.mcp && (
        <Tile
          to="/admin/mcp/servers"
          icon={<HubIcon fontSize="small" />}
          label={t("dashboard.integrations.mcp")}
          value={t("dashboard.integrations.healthy_of", { healthy: data.mcp.healthy, enabled: data.mcp.enabled })}
          warning={data.mcp.problems ? t("dashboard.integrations.problems", { count: data.mcp.problems }) : undefined}
        />
      )}
      {data.skills && (
        <Tile
          to="/admin/skills"
          icon={<AutoFixHighIcon fontSize="small" />}
          label={t("dashboard.integrations.skills")}
          value={t("dashboard.integrations.active_of", { active: data.skills.active, total: data.skills.total })}
        />
      )}
    </Box>
  );
}

function Tile({
  to,
  icon,
  label,
  value,
  warning,
  children,
}: {
  to: string;
  icon: ReactNode;
  label: string;
  value: string;
  warning?: string;
  children?: ReactNode;
}) {
  const theme = useTheme();
  return (
    <CardActionArea
      component={RouterLink}
      to={to}
      sx={{ borderRadius: 1, border: 1, borderColor: "divider", bgcolor: "dashboard.surfaceSubtle", p: 1.5, minHeight: 76 }}
    >
      <Stack direction="row" gap={1.25} alignItems="flex-start">
        <Box
          aria-hidden
          sx={{ width: 32, height: 32, borderRadius: 1, display: "grid", placeItems: "center", bgcolor: "action.hover", color: "primary.main", flexShrink: 0 }}
        >
          {icon}
        </Box>
        <Stack sx={{ minWidth: 0, flexGrow: 1 }} gap={0.25}>
          <Typography variant="caption" color="text.secondary">
            {label}
          </Typography>
          <Typography sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{value}</Typography>
          {warning && (
            <Typography variant="caption" sx={{ color: theme.palette.dashboard.health.degraded, fontWeight: 600 }}>
              {warning}
            </Typography>
          )}
          {children}
        </Stack>
      </Stack>
    </CardActionArea>
  );
}
