/**
 * Needs attention: the platform's current problems, most severe first.
 *
 * The server sends a code and parameters for each; the words are rendered
 * here, in the viewer's language (`dashboard.attention.<code>.title|detail`).
 * A problem clears when its cause does. Rules that could not run make the
 * widget "partial" rather than hiding what the others found.
 */
import { useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import type { AttentionItem, Severity } from "~/api/dashboard";
import { fmtAgo } from "../format";
import { useAttention } from "../queries";
import type { WidgetProps } from "../types";
import WidgetCard from "../WidgetCard";

const SHOWN = { phone: 4, other: 6 };

export default function AttentionWidget({ refreshToken }: WidgetProps) {
  const { t } = useTranslation();
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const [all, setAll] = useState(false);
  const query = useAttention({ refreshToken });
  const items = query.data?.items ?? [];
  const limit = isPhone ? SHOWN.phone : SHOWN.other;
  const visible = all ? items : items.slice(0, limit);

  return (
    <WidgetCard
      title={t("dashboard.widgets.attention.title")}
      subtitle={t("dashboard.widgets.attention.subtitle")}
      status={{
        loading: query.loading,
        error: query.error,
        hasData: Boolean(query.data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
        partial: query.data?.rule_errors.length ? [t("dashboard.attention.partial")] : null,
      }}
      skeleton={
        <Stack gap={1}>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} variant="rounded" height={52} />
          ))}
        </Stack>
      }
      empty={query.data && items.length === 0 ? <AllClear /> : undefined}
    >
      <List disablePadding sx={{ display: "grid", gap: 0.75 }}>
        {visible.map((item) => (
          <Item key={item.id} item={item} />
        ))}
      </List>
      {items.length > limit && (
        <Button onClick={() => setAll((v) => !v)} size="small" sx={{ mt: 1, minHeight: 44 }}>
          {all ? t("dashboard.attention.show_fewer") : t("dashboard.attention.show_all", { count: items.length })}
        </Button>
      )}
    </WidgetCard>
  );
}

function Item({ item }: { item: AttentionItem }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const params = localise(item.params, t);
  const hasMessage = typeof item.params.message === "string" && item.params.message.length > 0;
  const title = t(`dashboard.attention.${item.code}.title`, params);
  const detail = t(`dashboard.attention.${item.code}.${hasMessage ? "detail_message" : "detail"}`, {
    ...params,
    defaultValue: "",
  });
  const tone = { critical: "error", warning: "degraded", info: "maintenance" } as const;
  const color = theme.palette.dashboard.health[tone[item.severity]];

  const body = (
    <Box sx={{ display: "grid", gridTemplateColumns: "28px 1fr auto", gap: 1.25, alignItems: "center", width: "100%" }}>
      <Box
        sx={{ width: 28, height: 28, borderRadius: 1, display: "grid", placeItems: "center", color, bgcolor: `${color}1f` }}
        aria-label={t(`dashboard.attention.severity.${item.severity}`)}
        role="img"
      >
        <SeverityIcon severity={item.severity} />
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.3 }}>
          {title}
        </Typography>
        {detail && (
          // Two lines at most (a cluster's message can run long); all of it on hover.
          <Typography
            variant="caption"
            color="text.secondary"
            title={detail}
            sx={{
              lineHeight: 1.35,
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
              wordBreak: "break-word",
            }}
          >
            {detail}
          </Typography>
        )}
      </Box>
      {item.since && (
        <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
          {fmtAgo(item.since, i18n.language)}
        </Typography>
      )}
    </Box>
  );

  const sx = { borderRadius: 1, border: 1, borderColor: "divider", bgcolor: "dashboard.surfaceSubtle", px: 1.25, py: 1, minHeight: 48 };
  return item.link ? (
    <ListItemButton component={RouterLink} to={item.link} sx={sx}>
      {body}
    </ListItemButton>
  ) : (
    <Box component="li" sx={{ ...sx, listStyle: "none", display: "flex" }}>
      {body}
    </Box>
  );
}

function SeverityIcon({ severity }: { severity: Severity }) {
  if (severity === "critical") return <ErrorOutlineIcon fontSize="small" />;
  if (severity === "warning") return <WarningAmberIcon fontSize="small" />;
  return <InfoOutlinedIcon fontSize="small" />;
}

function AllClear() {
  const { t } = useTranslation();
  const theme = useTheme();
  return (
    <Stack alignItems="center" justifyContent="center" gap={1} sx={{ py: 3, height: "100%" }}>
      <CheckCircleOutlineIcon sx={{ color: theme.palette.dashboard.health.ok, fontSize: 36 }} />
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {t("dashboard.attention.all_clear")}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {t("dashboard.attention.all_clear_detail")}
      </Typography>
    </Stack>
  );
}

/** Parameters with enum values (a cluster's state, say) put into words. */
function localise(
  params: AttentionItem["params"],
  t: (key: string, options?: Record<string, unknown>) => string,
): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value == null) out[key] = "";
    else if (key === "state") out[key] = t(`dashboard.attention.states.${value}`, { defaultValue: String(value) });
    else out[key] = value;
  }
  return out;
}
