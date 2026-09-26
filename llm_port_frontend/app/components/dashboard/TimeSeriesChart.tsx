/**
 * A time series drawn the dashboard's way: soft areas, smooth lines, no
 * marks, a compact y axis, and a legend of coloured dots above. Used by the
 * dashboard's Usage card and by every page that charts over time.
 *
 * `fill`: grow into the height the card gives it (the dashboard's fill rows,
 * or a grid row beside a taller card) instead of a fixed height.
 */
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { LineChart } from "@mui/x-charts/LineChart";
import { useElementHeight } from "./fit";

export interface TimeSeries {
  id: string;
  label: string;
  data: number[];
  color: string;
  /** Shade under the line (default true). */
  area?: boolean;
}

export interface TimeSeriesChartProps {
  /** The x values: bucket labels ("2026-09-26") or dates. */
  x: (string | Date)[];
  /** How an x value is written on the axis. */
  xFormat: (value: string | Date) => string;
  series: TimeSeries[];
  /** Stack the series (parts of one total). */
  stacked?: boolean;
  /** A short description of the chart for screen readers. */
  ariaLabel: string;
  yFormat?: (value: number) => string;
  /** The legend of dots above (default: when there is more than one series). */
  legend?: boolean;
  /** Fixed height; default 190 (160 on a phone). The least height when filling. */
  height?: number;
  fill?: boolean;
}

export default function TimeSeriesChart({
  x,
  xFormat,
  series,
  stacked = false,
  ariaLabel,
  yFormat,
  legend,
  height,
  fill = false,
}: TimeSeriesChartProps) {
  const { i18n } = useTranslation();
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const [box, boxHeight] = useElementHeight<HTMLDivElement>();
  const base = height ?? (isPhone ? 160 : 190);
  const chartHeight = fill && boxHeight ? Math.max(base, boxHeight) : base;
  const compact = new Intl.NumberFormat(i18n.language, { notation: "compact" });
  const showLegend = legend ?? series.length > 1;
  // A dense axis on a phone shows every other label.
  const everyOther = isPhone && x.length > 8;

  return (
    <Box sx={fill ? { flex: 1, display: "flex", flexDirection: "column", minHeight: 0 } : undefined}>
      {showLegend && (
        <Stack direction="row" gap={1.5} sx={{ flexWrap: "wrap", mb: 0.5 }} component="ul" aria-hidden>
          {series.map((s) => (
            <Stack key={s.id} component="li" direction="row" alignItems="center" gap={0.75} sx={{ listStyle: "none" }}>
              <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: s.color }} />
              <Typography variant="caption">{s.label}</Typography>
            </Stack>
          ))}
        </Stack>
      )}
      <Box
        ref={fill ? box : undefined}
        role="img"
        aria-label={ariaLabel}
        sx={fill ? { flex: 1, minHeight: base, overflow: "hidden" } : undefined}
      >
        <LineChart
          height={chartHeight}
          margin={{ left: 8, right: 24, top: 8, bottom: 4 }}
          hideLegend
          skipAnimation
          xAxis={[
            {
              scaleType: "point",
              data: x,
              valueFormatter: (v: string | Date) => xFormat(v),
              tickInterval: (_v: string | Date, index: number) => !everyOther || index % 2 === 0,
            },
          ]}
          yAxis={[{ width: 40, valueFormatter: (v: number) => (yFormat ? yFormat(v) : compact.format(v)) }]}
          series={series.map((s) => ({
            id: s.id,
            label: s.label,
            data: s.data,
            color: s.color,
            area: s.area ?? true,
            ...(stacked ? { stack: "total" } : {}),
            showMark: false,
            curve: "monotoneX" as const,
          }))}
        />
      </Box>
    </Box>
  );
}
