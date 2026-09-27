/**
 * Cards in a grid with the dashboard's gap, for pages whose layout is fixed
 * (no Customize): one column on a phone, `columns` from `md` up. Cards in a
 * row stretch to one height; a TimeSeriesChart with `fill` grows into it.
 * Put `sx={FULL_ROW}` on a child's wrapper to span the row.
 */
import type { ReactNode } from "react";
import Box from "@mui/material/Box";
import { GRID_GAP_PX } from "./DashboardGrid";

export const FULL_ROW = { gridColumn: "1 / -1" } as const;

export default function PanelGrid({ children, columns = 2 }: { children: ReactNode; columns?: number }) {
  return (
    <Box
      sx={{
        display: "grid",
        gap: `${GRID_GAP_PX}px`,
        gridTemplateColumns: { xs: "minmax(0, 1fr)", md: `repeat(${columns}, minmax(0, 1fr))` },
        alignItems: "stretch",
      }}
    >
      {children}
    </Box>
  );
}
