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
  component: LazyExoticComponent<ComponentType<WidgetProps>>;
}
