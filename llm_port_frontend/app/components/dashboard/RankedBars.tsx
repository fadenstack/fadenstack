/**
 * A ranked list with a bar each: where requests went, what each model cost,
 * who used the most. The dashboard's "served at" list, shared.
 *
 * Bars are read against `share` (0..1): the caller decides whether that is a
 * part of a whole (percent of requests) or of the largest (spend by model).
 */
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";

export interface RankedBar {
  key: string;
  label: string;
  /** 0..1: how long the bar is. */
  share: number;
  /** What is printed at the end of the row ("42 %", "$1.20"). */
  valueLabel: string;
  /** Bar and dot colour; the theme's primary when absent. */
  color?: string;
}

export default function RankedBars({ items, barWidth = 72 }: { items: RankedBar[]; barWidth?: number }) {
  const theme = useTheme();
  return (
    <Stack gap={1.25} component="ol" sx={{ listStyle: "none", m: 0, p: 0, minWidth: 0 }}>
      {items.map((item) => {
        const color = item.color ?? theme.palette.primary.main;
        return (
          <Box
            key={item.key}
            component="li"
            sx={{ display: "grid", gridTemplateColumns: `minmax(0, 1fr) ${barWidth}px minmax(44px, auto)`, gap: 1, alignItems: "center" }}
          >
            <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
              <Box aria-hidden sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: color, flexShrink: 0 }} />
              <Typography variant="body2" noWrap sx={{ fontWeight: 600 }} title={item.label}>
                {item.label}
              </Typography>
            </Stack>
            <Box aria-hidden sx={{ height: 8, borderRadius: 999, bgcolor: "action.hover", overflow: "hidden" }}>
              <Box sx={{ width: `${Math.max(Math.min(item.share, 1) * 100, 2)}%`, height: "100%", bgcolor: color, borderRadius: 999 }} />
            </Box>
            <Typography variant="caption" sx={{ textAlign: "right", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
              {item.valueLabel}
            </Typography>
          </Box>
        );
      })}
    </Stack>
  );
}
