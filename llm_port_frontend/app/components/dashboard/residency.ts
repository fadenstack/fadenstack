/** Residency classes as the dashboard shows them: colour from the theme, label from i18n. */
import type { Theme } from "@mui/material/styles";
import type { ResidencyClass } from "~/api/dashboard";

export function residencyColor(theme: Theme, cls: ResidencyClass | null | undefined): string {
  return theme.palette.dashboard.residency[cls ?? "unknown"];
}

export const RESIDENCY_LABEL_KEYS: Record<ResidencyClass, string> = {
  machines: "dashboard.residency.machines",
  private: "dashboard.residency.private",
  external: "dashboard.residency.external",
  unknown: "dashboard.residency.unknown",
};
