/**
 * The widget grid: a 12-column flow whose rows fill themselves, so hiding a
 * widget never leaves a hole (spec §7.1). DOM order is the layout order --
 * no `grid-auto-flow: dense`, which reorders what is seen but not what a
 * keyboard or screen reader walks through.
 *
 * In Customize mode each widget gets a bar: drag handle (fine pointers),
 * size, move up / down, show / hide. Every control is 44 px on touch.
 *
 * Row height: widgets in a row stretch to one height. Content widgets
 * (tiles, the donut, gauges) set it; a fill widget (a list or chart that can
 * show more or less) beside one takes that height instead of adding its own
 * and fits its content to it. So a short content widget is not left with a
 * gap under it, and nothing is cut off or scrolls inside a card. A row with
 * no content widget (a fill widget on its own, as on a phone) sizes to
 * content as usual.
 *
 * Only the part of a fill widget that can stretch -- the chart, the list --
 * is size-contained (by the widget, with a least height). Its header, legend
 * and totals still count, so where they wrap (a half-width card on a medium
 * screen) the row grows to fit them rather than the card overflowing.
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Box from "@mui/material/Box";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ArrowDownwardIcon from "@mui/icons-material/ArrowDownward";
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import type { WidgetSize } from "~/api/dashboard";
import { SIZE_COLUMNS } from "~/lib/dashboardLayoutModel";
import type { WidgetDefinition } from "./types";

export interface GridItem {
  def: WidgetDefinition;
  size: WidgetSize;
  hidden: boolean;
  available: boolean;
  /** Why it is unavailable ("Requires the MCP module"). */
  reason?: string;
}

interface DashboardGridProps {
  items: GridItem[];
  editMode: boolean;
  onReorder: (activeId: string, overId: string) => void;
  onMove: (id: string, delta: -1 | 1) => void;
  onToggle: (id: string) => void;
  onSize: (id: string, size: WidgetSize) => void;
  renderWidget: (item: GridItem, fill: boolean) => ReactNode;
}

type Breakpoint = "xs" | "sm" | "md" | "lg";

function useBreakpoint(): Breakpoint {
  const theme = useTheme();
  const lg = useMediaQuery(theme.breakpoints.up("lg"));
  const md = useMediaQuery(theme.breakpoints.up("md"));
  const sm = useMediaQuery(theme.breakpoints.up("sm"));
  return lg ? "lg" : md ? "md" : sm ? "sm" : "xs";
}

/** Rows as flex-wrap packs them: in order, a new row when the next width does not fit. */
export function packRows(columns: number[]): number[][] {
  const rows: number[][] = [];
  let used = 12;
  columns.forEach((cols, index) => {
    if (used + cols > 12) {
      rows.push([]);
      used = 0;
    }
    rows[rows.length - 1].push(index);
    used += cols;
  });
  return rows;
}

/** The widgets that fill their row: fill widgets in a row with a content widget. */
export function fillingIds(widgets: { id: string; columns: number; fit?: "content" | "fill" }[]): Set<string> {
  const filling = new Set<string>();
  for (const row of packRows(widgets.map((w) => w.columns))) {
    const members = row.map((i) => widgets[i]);
    if (!members.some((w) => w.fit !== "fill")) continue;
    for (const w of members) if (w.fit === "fill") filling.add(w.id);
  }
  return filling;
}

export const GRID_GAP_PX = 12;

/** Touch devices get 44 px targets; a mouse can do with less. */
export const TOUCH_TARGET = { "@media (pointer: coarse)": { minWidth: 44, minHeight: 44 } } as const;

function basis(columns: number): string {
  return columns === 12 ? "100%" : `calc(${(columns / 12) * 100}% - ${GRID_GAP_PX}px)`;
}

export default function DashboardGrid({
  items,
  editMode,
  onReorder,
  onMove,
  onToggle,
  onSize,
  renderWidget,
}: DashboardGridProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const shown = editMode ? items : items.filter((i) => i.available && !i.hidden);
  const movable = items.filter((i) => i.available).map((i) => i.def.id);
  const breakpoint = useBreakpoint();
  const filling = fillingIds(
    shown.map((i) => ({
      id: i.def.id,
      columns: (editMode && (i.hidden || !i.available) ? SIZE_COLUMNS.XL : SIZE_COLUMNS[i.size])[breakpoint],
      // A widget shown as its Customize bar only has no content to fill with.
      fit: editMode && (i.hidden || !i.available) ? "content" : i.def.fit,
    })),
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (over && active.id !== over.id) onReorder(String(active.id), String(over.id));
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={shown.map((i) => i.def.id)} strategy={rectSortingStrategy}>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: `${GRID_GAP_PX}px`, alignItems: "stretch" }}>
          {shown.map((item) => (
            <SortableWidget
              key={item.def.id}
              item={item}
              editMode={editMode}
              first={movable[0] === item.def.id}
              last={movable[movable.length - 1] === item.def.id}
              onMove={onMove}
              onToggle={onToggle}
              onSize={onSize}
            >
              {item.available && !(editMode && item.hidden) ? renderWidget(item, filling.has(item.def.id)) : null}
            </SortableWidget>
          ))}
        </Box>
      </SortableContext>
    </DndContext>
  );
}

interface SortableWidgetProps {
  item: GridItem;
  editMode: boolean;
  first: boolean;
  last: boolean;
  onMove: (id: string, delta: -1 | 1) => void;
  onToggle: (id: string) => void;
  onSize: (id: string, size: WidgetSize) => void;
  children: ReactNode;
}

function SortableWidget({ item, editMode, first, last, onMove, onToggle, onSize, children }: SortableWidgetProps) {
  const { t } = useTranslation();
  const { def, size, hidden, available, reason } = item;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: def.id, disabled: !editMode || !available });
  // In Customize, a hidden or unavailable widget is only its bar: on a row of
  // its own, so the widgets that are shown keep the widths they really have.
  const collapsed = editMode && (hidden || !available);
  const cols = collapsed ? SIZE_COLUMNS.XL : SIZE_COLUMNS[size];
  const title = t(def.titleKey);

  return (
    <Box
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition, opacity: isDragging ? 0.4 : 1 }}
      sx={{
        flexGrow: 1,
        flexShrink: 1,
        minWidth: 0,
        flexBasis: { xs: basis(cols.xs), sm: basis(cols.sm), md: basis(cols.md), lg: basis(cols.lg) },
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
      }}
    >
      {editMode && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.25}
          sx={{
            px: 0.5,
            borderRadius: 1,
            minHeight: 44,
            bgcolor: hidden || !available ? "action.disabledBackground" : "action.hover",
          }}
        >
          <IconButton
            ref={setActivatorNodeRef}
            {...listeners}
            {...attributes}
            size="small"
            disabled={!available}
            aria-label={t("dashboard.customize_drag", { name: title })}
            sx={{
              cursor: "grab",
              display: "none",
              "@media (pointer: fine)": { display: "inline-flex" },
            }}
          >
            <DragIndicatorIcon fontSize="small" />
          </IconButton>
          <Typography
            variant="body2"
            noWrap
            sx={{ flexGrow: 1, pl: 0.5, fontWeight: 600, color: hidden || !available ? "text.disabled" : "text.primary" }}
          >
            {title}
            {!available && reason && (
              <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                {reason}
              </Typography>
            )}
          </Typography>
          {available && def.sizes.length > 1 && (
            <ToggleButtonGroup
              size="small"
              exclusive
              value={size}
              onChange={(_, value: WidgetSize | null) => value && onSize(def.id, value)}
              aria-label={t("dashboard.customize_size", { name: title })}
            >
              {def.sizes.map((s) => (
                <ToggleButton key={s} value={s} aria-label={t(`dashboard.size.${s}`)} sx={{ px: 1, ...TOUCH_TARGET }}>
                  {s}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          )}
          <Tooltip title={t("dashboard.move_up")}>
            <span>
              <IconButton
                size="small"
                disabled={!available || first}
                onClick={() => onMove(def.id, -1)}
                aria-label={t("dashboard.move_up_named", { name: title })}
                sx={TOUCH_TARGET}
              >
                <ArrowUpwardIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title={t("dashboard.move_down")}>
            <span>
              <IconButton
                size="small"
                disabled={!available || last}
                onClick={() => onMove(def.id, 1)}
                aria-label={t("dashboard.move_down_named", { name: title })}
                sx={TOUCH_TARGET}
              >
                <ArrowDownwardIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title={hidden ? t("dashboard.show_section") : t("dashboard.hide_section")}>
            <span>
              <IconButton
                size="small"
                disabled={!available}
                onClick={() => onToggle(def.id)}
                aria-label={t(hidden ? "dashboard.show_named" : "dashboard.hide_named", { name: title })}
                sx={TOUCH_TARGET}
              >
                {hidden ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
              </IconButton>
            </span>
          </Tooltip>
        </Stack>
      )}
      {children && <Box sx={{ flexGrow: 1, minWidth: 0 }}>{children}</Box>}
    </Box>
  );
}
