/**
 * The frame every dashboard widget renders in: title, link, and the states a
 * widget can be in -- loading, error, empty, partial, stale -- drawn the same
 * way everywhere. An error is the widget's own: it never takes the page down.
 */
import type { ReactNode } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import Chip from "@mui/material/Chip";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { fmtAgo } from "./format";

export interface WidgetCardStatus {
  /** Nothing to show yet. */
  loading?: boolean;
  /** The last fetch failed. With no data: shown in place of the content. */
  error?: string | null;
  /** True when older data is on screen (an error then only adds a badge). */
  hasData?: boolean;
  onRetry?: () => void;
  /** What is missing from a partial answer, one line each. */
  partial?: string[] | null;
  /** Data older than it should be. */
  stale?: boolean;
  updatedAt?: number | string | null;
}

export interface WidgetCardProps {
  title: string;
  subtitle?: string;
  /** Where "View →" goes; `external` opens a new tab (Grafana). */
  link?: { to: string; label: string; external?: boolean };
  /** Controls on the right of the header (a window selector, …). */
  actions?: ReactNode;
  status?: WidgetCardStatus;
  /** Placeholder shaped like the content, shown while loading. */
  skeleton?: ReactNode;
  /** Shown instead of the content when there is nothing to show. */
  empty?: ReactNode;
  children?: ReactNode;
}

export default function WidgetCard({
  title,
  subtitle,
  link,
  actions,
  status = {},
  skeleton,
  empty,
  children,
}: WidgetCardProps) {
  const { t, i18n } = useTranslation();
  const { loading, error, hasData, onRetry, partial, stale, updatedAt } = status;
  const failedWithoutData = Boolean(error) && !hasData && !loading;

  let body: ReactNode;
  if (loading) {
    body = skeleton ?? <DefaultSkeleton />;
  } else if (failedWithoutData) {
    body = (
      <Alert
        severity="error"
        action={
          onRetry ? (
            <Button color="inherit" size="small" onClick={onRetry} sx={{ minHeight: 36 }}>
              {t("dashboard.retry")}
            </Button>
          ) : undefined
        }
      >
        {t("dashboard.widget_failed")}
      </Alert>
    );
  } else if (empty) {
    body = empty;
  } else {
    body = children;
  }

  return (
    <Card
      variant="outlined"
      component="section"
      aria-label={title}
      sx={{ height: "100%", display: "flex", flexDirection: "column", minWidth: 0 }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        justifyContent="space-between"
        gap={1}
        sx={{ px: 2, pt: 1.75, pb: 1, flexWrap: "wrap" }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h2" variant="subtitle1" sx={{ fontWeight: 700, lineHeight: 1.3 }}>
            {title}
          </Typography>
          {subtitle && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        <Stack direction="row" alignItems="center" gap={0.75} sx={{ flexWrap: "wrap" }}>
          {partial && partial.length > 0 && (
            <Tooltip title={partial.join("\n")}>
              <Chip size="small" variant="outlined" color="warning" label={t("dashboard.partial")} />
            </Tooltip>
          )}
          {error && hasData && (
            <Tooltip title={t("dashboard.refresh_failed_detail")}>
              <Chip
                size="small"
                variant="outlined"
                color="warning"
                label={t("dashboard.refresh_failed")}
                onClick={onRetry}
              />
            </Tooltip>
          )}
          {stale && updatedAt != null && (
            <Typography variant="caption" color="text.secondary">
              {t("dashboard.as_of", { ago: fmtAgo(updatedAt, i18n.language) })}
            </Typography>
          )}
          {actions}
          {link &&
            (link.external ? (
              <Button
                component="a"
                href={link.to}
                target="_blank"
                rel="noreferrer"
                size="small"
                endIcon={<ArrowForwardIcon fontSize="small" />}
                sx={{ minHeight: 36, whiteSpace: "nowrap" }}
              >
                {link.label}
              </Button>
            ) : (
              <Button
                component={RouterLink}
                to={link.to}
                size="small"
                endIcon={<ArrowForwardIcon fontSize="small" />}
                sx={{ minHeight: 36, whiteSpace: "nowrap" }}
              >
                {link.label}
              </Button>
            ))}
        </Stack>
      </Stack>
      <Box sx={{ px: 2, pb: 2, flexGrow: 1, minWidth: 0, overflow: "auto" }}>{body}</Box>
    </Card>
  );
}

function DefaultSkeleton() {
  return (
    <Stack gap={1}>
      <Skeleton variant="rounded" height={28} />
      <Skeleton variant="rounded" height={28} />
      <Skeleton variant="rounded" height={28} width="60%" />
    </Stack>
  );
}

/** A centred message with an optional call to action, for empty widgets. */
export function WidgetEmpty({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      gap={1}
      sx={{
        py: 3,
        px: 2,
        textAlign: "center",
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: 1,
        height: "100%",
      }}
    >
      <Typography variant="body2" color="text.secondary">
        {message}
      </Typography>
      {action}
    </Stack>
  );
}
