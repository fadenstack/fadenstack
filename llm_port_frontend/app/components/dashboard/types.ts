import type { ComponentType, LazyExoticComponent } from "react";
import type { DashboardLayout, WidgetSize } from "~/api/dashboard";
import type { LayoutWidgetSpec, Profile } from "~/lib/dashboardLayoutModel";

/** What every widget component receives. */
export interface WidgetProps {
  size: WidgetSize;
  layout: DashboardLayout;
  /** Change the layout's view settings (traffic window, fleet colouring, …). */
  onLayoutChange: (patch: Partial<DashboardLayout>) => void;
  /** Bumped by the header's Refresh button. */
  refreshToken: number;
  /**
   * The row sets this widget's height (a `fit: "fill"` widget beside a
   * content-sized one): fill it -- grow the chart, show the list items that
   * fit -- rather than size to the content.
   */
  fill?: boolean;
}

/**
 * One dashboard widget. Adding a widget is one of these, one component and,
 * when it needs one, one endpoint -- the page itself does not change.
 */
export interface WidgetDefinition extends LayoutWidgetSpec {
  titleKey: string;
  descriptionKey: string;
  requires?: {
    /** Services-manifest module name: "mcp", "skills", "rag_lite", "pii", "chat". */
    module?: string;
    /** Only with RAG Pro (`useRagMode() === "pro"`). */
    ragPro?: boolean;
  };
  defaultVisible: (profile: Profile) => boolean;
  /**
   * "content" (the default): the widget's content sets its height, and a row
   * is as tall as its tallest such widget. "fill": a list or chart that can
   * show more or less; beside a content widget it takes the row's height
   * instead of setting it, so no widget in the row is left with empty space.
   * With `fill` set the widget size-contains its stretchable part (chart,
   * list) with a least height; the rest of it counts as usual.
   */
  fit?: "content" | "fill";
  component: LazyExoticComponent<ComponentType<WidgetProps>>;
}
