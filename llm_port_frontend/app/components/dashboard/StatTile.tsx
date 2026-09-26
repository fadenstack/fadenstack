/**
 * A number with its label: the dashboard's key-metric tile, shared with every
 * page that shows figures (observability, PII, a deployment, …) so they read
 * as one product.
 *
 * Rules it keeps, as the dashboard does:
 * - A gap is never a zero: no value is "–", dimmed, with the reason on hover.
 * - Loading is not absence: a skeleton until the first answer.
 */
import type { ReactNode } from "react";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";

export interface TrendInfo {
  /** "+12 %" -- formatted by the caller. */
  text: string;
  direction: "up" | "down" | "flat";
  /** Whether the change is good news; up is not always good (errors). */
  tone: "good" | "bad" | "neutral";
  /** What it is compared with ("vs yesterday"), for the tooltip. */
  baseline: string;
}

export interface StatTileProps {
  label: string;
  /** The formatted value; null when there is nothing to show. */
  value: string | null;
  /** A line under the value: context, a breakdown, a unit. */
  detail?: ReactNode;
  trend?: TrendInfo | null;
  /** Colours the value, for a figure that is itself a warning. */
  tone?: "default" | "warning" | "error" | "ok";
  loading?: boolean;
  /** Why there is no value, on hover. */
  emptyHint?: string;
  /**
   * "kpi" (default): label, large value, detail. "compact": value over a
   * small-caps label, for a strip of secondary figures (p50, p95, …).
   */
  variant?: "kpi" | "compact";
}

/** One line where there is room; on a phone's half-width tile, wrap rather than cut words. */
const FIT = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: { xs: "normal", sm: "nowrap" },
} as const;

const SURFACE = {
  borderRadius: 1,
  border: 1,
  borderColor: "divider",
  bgcolor: "dashboard.surfaceSubtle",
  minWidth: 0,
} as const;

export default function StatTile({
  label,
  value,
  detail,
  trend,
  tone = "default",
  loading = false,
  emptyHint,
  variant = "kpi",
}: StatTileProps) {
  const theme = useTheme();
  const hasValue = value !== null;
  const color =
    !hasValue
      ? "text.disabled"
      : tone === "warning"
        ? theme.palette.dashboard.health.degraded
        : tone === "error"
          ? theme.palette.dashboard.health.error
          : tone === "ok"
            ? theme.palette.dashboard.health.ok
            : "text.primary";
  const shown = loading && !hasValue ? null : (value ?? "–");

  if (variant === "compact") {
    return (
      <Tooltip title={!hasValue && !loading ? (emptyHint ?? "") : ""}>
        <Box sx={{ ...SURFACE, px: 1.25, py: 1 }}>
          {shown === null ? (
            <Skeleton width={48} height={24} />
          ) : (
            <Typography sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums", color }}>{shown}</Typography>
          )}
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ textTransform: "uppercase", letterSpacing: 0.4, display: "block", ...FIT }}
          >
            {label}
          </Typography>
        </Box>
      </Tooltip>
    );
  }

  return (
    <Tooltip title={!hasValue && !loading ? (emptyHint ?? "") : ""}>
      <Box sx={{ ...SURFACE, p: 1.5 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="baseline" gap={1}>
          <Typography variant="caption" color="text.secondary" sx={FIT}>
            {label}
          </Typography>
          {trend && <TrendBadge {...trend} />}
        </Stack>
        {shown === null ? (
          <Skeleton width="60%" height={34} />
        ) : (
          <Typography
            component="p"
            sx={{ fontSize: { xs: 22, sm: 26 }, fontWeight: 700, lineHeight: 1.25, mt: 0.5, fontVariantNumeric: "tabular-nums", color }}
            noWrap
          >
            {shown}
          </Typography>
        )}
        {detail != null && (
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.25, ...FIT }}>
            {detail}
          </Typography>
        )}
      </Box>
    </Tooltip>
  );
}

/** ▲ +12 % in the tone of the news, the baseline on hover. */
export function TrendBadge({ text, direction, tone, baseline }: TrendInfo) {
  const theme = useTheme();
  const color =
    tone === "good"
      ? theme.palette.dashboard.health.ok
      : tone === "bad"
        ? theme.palette.dashboard.health.error
        : theme.palette.text.secondary;
  const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "▬";
  return (
    <Tooltip title={baseline}>
      <Typography
        variant="caption"
        sx={{ color, fontWeight: 700, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
        aria-label={`${text} ${baseline}`}
      >
        <span aria-hidden>{arrow} </span>
        {text}
      </Typography>
    </Tooltip>
  );
}

/**
 * Tiles in a grid: as many columns as fit (`min` px each); two on a phone,
 * where an odd last tile spans both.
 */
export function StatGrid({ children, min = 170, gap = 1.5 }: { children: ReactNode; min?: number; gap?: number }) {
  return (
    <Box
      sx={{
        display: "grid",
        gap,
        gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", sm: `repeat(auto-fit, minmax(${min}px, 1fr))` },
        "& > :last-of-type:nth-of-type(odd)": { gridColumn: { xs: "span 2", sm: "auto" } },
      }}
    >
      {children}
    </Box>
  );
}
