import { describe, expect, it } from "vitest";
import {
  DEFAULT_KPIS,
  defaultLayout,
  migrateV1,
  moveWidget,
  reconcile,
  setSize,
  toggleHidden,
  type LayoutWidgetSpec,
} from "./dashboardLayoutModel";

const specs: LayoutWidgetSpec[] = [
  { id: "kpis", sizes: ["XL"], defaultSize: "XL", defaultVisible: () => true },
  { id: "capacity", sizes: ["M", "L", "XL"], defaultSize: "M", defaultVisible: () => true },
  { id: "platform_health", sizes: ["XL"], defaultSize: "XL", defaultVisible: () => true },
  { id: "server_resources", sizes: ["M", "L"], defaultSize: "M", defaultVisible: (p) => p !== "enterprise" },
  { id: "top_containers", sizes: ["M", "L"], defaultSize: "M", defaultVisible: () => false },
  { id: "integrations", sizes: ["S", "M"], defaultSize: "M", defaultVisible: () => true },
];

describe("defaults", () => {
  it("follow the registry order and the profile's visibility", () => {
    const team = defaultLayout(specs, "team");
    expect(team.order).toEqual(specs.map((s) => s.id));
    expect(team.hidden).toEqual(["top_containers"]);
    expect(team.kpis).toEqual(DEFAULT_KPIS);
    expect(defaultLayout(specs, "enterprise").hidden).toEqual(["server_resources", "top_containers"]);
  });
});

describe("reconcile", () => {
  it("drops ids this build does not know and appends widgets it does not have", () => {
    const stored = { order: ["platform_health", "gone_widget", "kpis"], hidden: ["gone_widget"] };
    const layout = reconcile(stored, specs, "team");
    expect(layout.order).toEqual([
      "platform_health",
      "kpis",
      "capacity",
      "server_resources",
      "top_containers",
      "integrations",
    ]);
    // Appended widgets take their default visibility.
    expect(layout.hidden).toEqual(["top_containers"]);
  });

  it("keeps a widget's place even while its module is off", () => {
    // Availability is decided at render time; the order is left alone.
    const stored = { order: ["integrations", "kpis"], hidden: [] };
    expect(reconcile(stored, specs, "team").order.slice(0, 2)).toEqual(["integrations", "kpis"]);
  });

  it("drops sizes a widget does not allow, unknown KPIs and bad view settings", () => {
    const layout = reconcile(
      {
        order: [],
        sizes: { capacity: "L", platform_health: "S" as never },
        kpis: ["requests_today", "made_up", "requests_today"],
        traffic_window: "1y" as never,
      },
      specs,
      "team",
    );
    expect(layout.sizes).toEqual({ capacity: "L" });
    expect(layout.kpis).toEqual(["requests_today"]);
    expect(layout.traffic_window).toEqual("today");
  });
});

describe("migrating the v1 layout", () => {
  it("maps section ids, merges two into one at the earlier place, and keeps visibility", () => {
    const layout = migrateV1(
      {
        order: ["module_status", "gauges", "dependency_health", "quick_links", "node_fleet"],
        hidden: ["module_status", "node_fleet"],
      },
      specs,
      "team",
    );
    // module_status and dependency_health became platform_health, at module_status's place;
    // quick_links is gone.
    expect(layout.order.slice(0, 3)).toEqual(["platform_health", "server_resources", "capacity"]);
    // Visible because dependency_health was; capacity was hidden as node_fleet.
    expect(layout.hidden).not.toContain("platform_health");
    expect(layout.hidden).toContain("capacity");
  });
});

describe("edits", () => {
  it("move among the widgets the user can see, skipping unavailable ones", () => {
    const layout = defaultLayout(specs, "team");
    const visible = ["kpis", "capacity", "server_resources"]; // platform_health unavailable here
    const moved = moveWidget(layout, "server_resources", -1, visible);
    expect(moved.order.indexOf("server_resources")).toBeLessThan(moved.order.indexOf("capacity"));
    expect(moveWidget(layout, "kpis", -1, visible)).toBe(layout);
  });

  it("toggle visibility and keep hidden in layout order", () => {
    const layout = toggleHidden(toggleHidden(defaultLayout(specs, "team"), "kpis"), "top_containers");
    expect(layout.hidden).toEqual(["kpis"]);
  });

  it("store only sizes that differ from the default", () => {
    const capacity = specs[1];
    const big = setSize(defaultLayout(specs, "team"), capacity, "L");
    expect(big.sizes).toEqual({ capacity: "L" });
    expect(setSize(big, capacity, "M").sizes).toEqual({});
    expect(setSize(big, capacity, "S")).toBe(big); // not allowed for this widget
  });
});
