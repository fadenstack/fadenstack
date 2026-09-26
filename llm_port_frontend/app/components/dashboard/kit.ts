/**
 * The dashboard's parts, for any page that shows figures: the card frame,
 * tiles, charts, ranked bars, a range toggle, the page header and a grid.
 * Built for the dashboard; imported from here so every page matches it.
 */
export { default as WidgetCard, WidgetEmpty, type WidgetCardProps, type WidgetCardStatus } from "./WidgetCard";
export { default as StatTile, StatGrid, TrendBadge, type StatTileProps, type TrendInfo } from "./StatTile";
export { default as RankedBars, type RankedBar } from "./RankedBars";
export { default as TimeSeriesChart, type TimeSeries, type TimeSeriesChartProps } from "./TimeSeriesChart";
export { default as RangeToggle } from "./RangeToggle";
export { default as PageHeader } from "./PageHeader";
export { default as PanelGrid, FULL_ROW } from "./PanelGrid";
