/**
 * The dashboard layout as data: defaults per profile, reconciling a stored
 * layout with the widgets this build has, migrating the v1 layout, and the
 * edits Customize makes. Pure functions, so the rules are tested without React.
 */
import type { DashboardLayout, WidgetSize } from "~/api/dashboard";

export type Profile = "private" | "team" | "enterprise";

/** What the layout rules need to know about a widget. */
export interface LayoutWidgetSpec {
  id: string;
  sizes: WidgetSize[];
  defaultSize: WidgetSize;
  defaultVisible: (profile: Profile) => boolean;
}

export const KPI_IDS = [
  "requests_today",
  "active_users_today",
  "in_house_share_today",
  "spend_month",
  "attention_open",
  "error_rate_1h",
  "gpus_busy",
] as const;
export type KpiId = (typeof KPI_IDS)[number];
export const DEFAULT_KPIS: KpiId[] = [
  "requests_today",
  "active_users_today",
  "in_house_share_today",
  "spend_month",
  "attention_open",
];
export const MAX_KPIS = 6;

export function defaultLayout(specs: LayoutWidgetSpec[], profile: Profile): DashboardLayout {
  return {
    version: 2,
    order: specs.map((s) => s.id),
    hidden: specs.filter((s) => !s.defaultVisible(profile)).map((s) => s.id),
    sizes: {},
    kpis: [...DEFAULT_KPIS],
    traffic_window: "today",
    usage_range: "7d",
    fleet_color_by: "health",
  };
}

/**
 * A stored layout, made to fit this build's widgets.
 *
 * Ids this build does not know are dropped; widgets it has that the layout
 * does not are appended with their default visibility. Widgets whose module
 * is off are *not* removed -- availability is decided at render time -- so a
 * module switched back on brings its widget back where the user left it.
 */
export function reconcile(
  stored: Partial<DashboardLayout> | null | undefined,
  specs: LayoutWidgetSpec[],
  profile: Profile,
): DashboardLayout {
  const base = defaultLayout(specs, profile);
  if (!stored) return base;
  const byId = new Map(specs.map((s) => [s.id, s]));
  const known = (id: string) => byId.has(id);

  const order = unique((stored.order ?? []).filter(known));
  const placed = new Set(order);
  const hidden = new Set(unique((stored.hidden ?? []).filter(known)));
  for (const spec of specs) {
    if (placed.has(spec.id)) continue;
    order.push(spec.id);
    if (!spec.defaultVisible(profile)) hidden.add(spec.id);
  }

  const sizes: Record<string, WidgetSize> = {};
  for (const [id, size] of Object.entries(stored.sizes ?? {})) {
    const spec = byId.get(id);
    if (spec && spec.sizes.includes(size)) sizes[id] = size;
  }

  const knownKpis = new Set<string>(KPI_IDS);
  const kpis = unique((stored.kpis ?? base.kpis).filter((k) => knownKpis.has(k))).slice(0, MAX_KPIS);

  return {
    version: 2,
    order,
    hidden: order.filter((id) => hidden.has(id)),
    sizes,
    kpis,
    traffic_window: oneOf(stored.traffic_window, ["today", "24h", "7d", "30d"], base.traffic_window),
    usage_range: oneOf(stored.usage_range, ["7d", "30d"], base.usage_range),
    fleet_color_by: oneOf(stored.fleet_color_by, ["health", "gpu_util", "vram_allocated"], base.fleet_color_by),
  };
}

/** v1 section ids (the localStorage layout before the redesign) -> v2 widget ids. */
const V1_TO_V2: Record<string, string | null> = {
  gauges: "server_resources",
  node_fleet: "capacity",
  stat_cards: "kpis",
  grafana: "grafana",
  dependency_health: "platform_health",
  module_status: "platform_health",
  data_residency: "integrations",
  top_containers: "top_containers",
  quick_links: null,
};

/**
 * The v1 layout in v2 terms. Two v1 sections that became one widget put it
 * at the earlier position, visible if either was.
 */
export function migrateV1(
  v1: { order: string[]; hidden: string[] },
  specs: LayoutWidgetSpec[],
  profile: Profile,
): DashboardLayout {
  const order: string[] = [];
  const visible = new Set<string>();
  const seen = new Set<string>();
  for (const oldId of v1.order) {
    const id = V1_TO_V2[oldId];
    if (!id) continue;
    if (!seen.has(id)) {
      seen.add(id);
      order.push(id);
    }
    if (!v1.hidden.includes(oldId)) visible.add(id);
  }
  const hidden = order.filter((id) => !visible.has(id));
  return reconcile({ order, hidden }, specs, profile);
}

/** Move *id* one place up (-1) or down (+1) among the widgets the user can see. */
export function moveWidget(layout: DashboardLayout, id: string, delta: -1 | 1, among: string[]): DashboardLayout {
  const visible = among.filter((w) => layout.order.includes(w));
  const at = visible.indexOf(id);
  const target = visible[at + delta];
  if (at < 0 || target === undefined) return layout;
  return reorder(layout, id, target);
}

/** Put *activeId* where *overId* is (drag and drop). */
export function reorder(layout: DashboardLayout, activeId: string, overId: string): DashboardLayout {
  const from = layout.order.indexOf(activeId);
  const to = layout.order.indexOf(overId);
  if (from < 0 || to < 0 || from === to) return layout;
  const order = [...layout.order];
  order.splice(from, 1);
  order.splice(to, 0, activeId);
  return { ...layout, order };
}

export function toggleHidden(layout: DashboardLayout, id: string): DashboardLayout {
  const hidden = layout.hidden.includes(id)
    ? layout.hidden.filter((h) => h !== id)
    : [...layout.hidden, id];
  return { ...layout, hidden: layout.order.filter((w) => hidden.includes(w)) };
}

export function setSize(layout: DashboardLayout, spec: LayoutWidgetSpec, size: WidgetSize): DashboardLayout {
  if (!spec.sizes.includes(size)) return layout;
  const sizes = { ...layout.sizes };
  if (size === spec.defaultSize) delete sizes[spec.id];
  else sizes[spec.id] = size;
  return { ...layout, sizes };
}

export function sizeOf(layout: DashboardLayout, spec: LayoutWidgetSpec): WidgetSize {
  const chosen = layout.sizes[spec.id];
  return chosen && spec.sizes.includes(chosen) ? chosen : spec.defaultSize;
}

/** Twelfths of the row a size takes at each breakpoint (spec §7.1). */
export const SIZE_COLUMNS: Record<WidgetSize, { xs: number; sm: number; md: number; lg: number }> = {
  S: { xs: 12, sm: 6, md: 6, lg: 4 },
  M: { xs: 12, sm: 12, md: 6, lg: 6 },
  L: { xs: 12, sm: 12, md: 12, lg: 8 },
  XL: { xs: 12, sm: 12, md: 12, lg: 12 },
};

function unique<T>(items: T[]): T[] {
  return [...new Set(items)];
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}
