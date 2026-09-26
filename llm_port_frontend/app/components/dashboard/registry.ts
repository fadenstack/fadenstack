/**
 * The dashboard's widgets, in their default order.
 *
 * Components are lazy, so a widget's code is only downloaded when it is shown:
 * a module that is off costs nothing.
 */
import { lazy } from "react";
import type { WidgetDefinition } from "./types";

const always = () => true;

export const WIDGETS: WidgetDefinition[] = [
  {
    id: "kpis",
    titleKey: "dashboard.widgets.kpis.title",
    descriptionKey: "dashboard.widgets.kpis.description",
    sizes: ["XL"],
    defaultSize: "XL",
    defaultVisible: always,
    component: lazy(() => import("./widgets/KpisWidget")),
  },
  {
    id: "traffic",
    titleKey: "dashboard.widgets.traffic.title",
    descriptionKey: "dashboard.widgets.traffic.description",
    sizes: ["M", "L", "XL"],
    defaultSize: "L",
    defaultVisible: always,
    component: lazy(() => import("./widgets/TrafficWidget")),
  },
  {
    id: "capacity",
    titleKey: "dashboard.widgets.capacity.title",
    descriptionKey: "dashboard.widgets.capacity.description",
    sizes: ["M", "L", "XL"],
    defaultSize: "M",
    defaultVisible: always,
    component: lazy(() => import("./widgets/CapacityWidget")),
  },
  {
    id: "usage",
    titleKey: "dashboard.widgets.usage.title",
    descriptionKey: "dashboard.widgets.usage.description",
    sizes: ["M", "L", "XL"],
    defaultSize: "M",
    defaultVisible: always,
    component: lazy(() => import("./widgets/UsageWidget")),
  },
  {
    id: "platform_health",
    titleKey: "dashboard.widgets.platform_health.title",
    descriptionKey: "dashboard.widgets.platform_health.description",
    sizes: ["XL"],
    defaultSize: "XL",
    defaultVisible: always,
    component: lazy(() => import("./widgets/PlatformHealthWidget")),
  },
  {
    id: "server_resources",
    titleKey: "dashboard.widgets.server_resources.title",
    descriptionKey: "dashboard.widgets.server_resources.description",
    sizes: ["M", "L"],
    defaultSize: "M",
    defaultVisible: (profile) => profile !== "enterprise",
    component: lazy(() => import("./widgets/ServerResourcesWidget")),
  },
  {
    id: "top_containers",
    titleKey: "dashboard.widgets.top_containers.title",
    descriptionKey: "dashboard.widgets.top_containers.description",
    sizes: ["M", "L"],
    defaultSize: "M",
    defaultVisible: () => false,
    component: lazy(() => import("./widgets/TopContainersWidget")),
  },
  {
    id: "grafana",
    titleKey: "dashboard.widgets.grafana.title",
    descriptionKey: "dashboard.widgets.grafana.description",
    sizes: ["XL"],
    defaultSize: "XL",
    defaultVisible: () => false,
    component: lazy(() => import("./widgets/GrafanaWidget")),
  },
];
