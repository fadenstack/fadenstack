/**
 * The page's refresh control: a refresh button inside a ring that fills as
 * the next automatic refresh approaches.
 *
 * The ring's colour says how fresh the figures are -- green within one refresh
 * interval, amber when a refresh is overdue, red when the figures are stale or
 * the last refresh failed -- and it spins while refreshing. The words ("updated
 * 12 s ago") are in the tooltip and the accessible name, not on the bar.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import { useTheme } from "@mui/material/styles";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useReducedMotion } from "~/lib/useReducedMotion";
import { fmtAgo } from "./format";

interface RefreshRingProps {
  /** When the page's figures were last fetched (ms since epoch). */
  updatedAt: number | null;
  /** How often they refresh on their own. */
  intervalMs: number;
  refreshing: boolean;
  failed: boolean;
  onRefresh: () => void;
}

type Freshness = "fresh" | "late" | "stale";

const SIZE = 34;

export default function RefreshRing({ updatedAt, intervalMs, refreshing, failed, onRefresh }: RefreshRingProps) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const reducedMotion = useReducedMotion();
  const now = useNow(1000);

  const elapsed = updatedAt === null ? 0 : Math.max(0, now - updatedAt);
  const freshness: Freshness =
    failed || elapsed > intervalMs * 3 ? "stale" : elapsed > intervalMs * 1.5 ? "late" : "fresh";
  const color = {
    fresh: theme.palette.dashboard.health.ok,
    late: theme.palette.dashboard.health.degraded,
    stale: theme.palette.dashboard.health.error,
  }[freshness];
  // Fills from empty (just refreshed) to full (the next refresh is due).
  const progress = updatedAt === null ? 0 : Math.min(100, (elapsed / intervalMs) * 100);

  const label = refreshing
    ? t("dashboard.refreshing")
    : failed
      ? t("dashboard.refresh_failed_tooltip")
      : updatedAt === null
        ? t("dashboard.refresh")
        : t("dashboard.refresh_tooltip", {
            ago: fmtAgo(updatedAt, i18n.language, now),
            seconds: Math.round(intervalMs / 1000),
          });

  return (
    <Tooltip title={label} arrow>
      <Box sx={{ position: "relative", width: SIZE, height: SIZE, flexShrink: 0 }}>
        <CircularProgress
          variant="determinate"
          value={100}
          size={SIZE}
          thickness={2.5}
          aria-hidden
          sx={{ position: "absolute", inset: 0, color: "action.hover" }}
        />
        <CircularProgress
          variant={refreshing && !reducedMotion ? "indeterminate" : "determinate"}
          value={refreshing ? 25 : progress}
          size={SIZE}
          thickness={2.5}
          aria-hidden
          sx={{
            position: "absolute",
            inset: 0,
            color,
            // The ring advances once a second; a short ease hides the steps.
            "& .MuiCircularProgress-circle": { transition: reducedMotion ? "none" : "stroke-dashoffset 900ms linear" },
          }}
        />
        <IconButton
          size="small"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label={label}
          sx={{
            position: "absolute",
            inset: 3,
            width: SIZE - 6,
            height: SIZE - 6,
            color: "text.primary",
            // The whole ring is the target on a touch screen.
            "@media (pointer: coarse)": { inset: -5, width: SIZE + 10, height: SIZE + 10 },
          }}
        >
          <RefreshIcon sx={{ fontSize: 18 }} />
        </IconButton>
      </Box>
    </Tooltip>
  );
}

/** The current time, updated every *ms*; only this component re-renders. */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}
