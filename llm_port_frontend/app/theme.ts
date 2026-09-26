import { alpha, createTheme } from "@mui/material/styles";
import type { PaletteMode } from "@mui/material";
import { grey } from "@mui/material/colors";

/**
 * Colours the dashboard needs beyond MUI's roles, per mode.
 *
 * Each is derived from the palette below, and each passes 3:1 against
 * `background.paper` in its mode (checked 2026-09-26): the light mode takes the
 * darker variant wherever the main one does not (`warning.main` on white is
 * 2.4:1). The load scale is the exception by design -- a lightly used GPU is
 * meant to look faint -- and is always shown with its numbers.
 */
export interface DashboardPalette {
  /** Where a request was served (see services/llm/residency.py). */
  residency: { machines: string; private: string; external: string; unknown: string };
  /** A machine's reported state. Never derived from load. */
  health: {
    ok: string;
    degraded: string;
    error: string;
    maintenance: string;
    offline: string;
    stale: string;
  };
  /** GPU utilization / VRAM allocated, low to high. Neutral: never warning colours. */
  load: [string, string, string, string, string];
  /** Inner tiles inside a widget card. */
  surfaceSubtle: string;
}

declare module "@mui/material/styles" {
  interface Palette {
    dashboard: DashboardPalette;
  }
  interface PaletteOptions {
    dashboard?: DashboardPalette;
  }
}

export function getAppTheme(mode: PaletteMode) {
  const dark = mode === "dark";

  const base = createTheme({
    palette: {
      mode,
      primary: {
        main: "#7c4dff",
        light: "#b47cff",
        dark: "#3f1dcb",
      },
      secondary: {
        main: "#00b8d4",
        light: "#6effff",
        dark: "#0088a3",
      },
      background: dark
        ? {
            default: "#0a0e1a",
            paper: "#111827",
          }
        : {
            default: "#f5f7fb",
            paper: "#ffffff",
          },
      error: { main: "#e53935" },
      warning: { main: "#fb8c00" },
      success: { main: "#2e7d32" },
      text: dark
        ? {
            primary: "#e0e0e0",
            secondary: "#9e9e9e",
          }
        : {
            primary: "#1a1f2f",
            secondary: "#4b5565",
          },
      divider: dark ? "rgba(255,255,255,0.08)" : "rgba(18,28,45,0.12)",
    },
    typography: {
      fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
      h4: { fontWeight: 700 },
      h5: { fontWeight: 700 },
      h6: { fontWeight: 600 },
    },
    shape: {
      borderRadius: 12,
    },
    components: {
      MuiDrawer: {
        styleOverrides: {
          paper: ({ theme }) => ({
            backgroundColor: theme.palette.background.paper,
            borderRight: `1px solid ${theme.palette.divider}`,
          }),
        },
      },
      MuiAppBar: {
        styleOverrides: {
          root: ({ theme }) => ({
            backgroundColor: theme.palette.background.paper,
            borderBottom: `1px solid ${theme.palette.divider}`,
          }),
        },
      },
      MuiTableHead: {
        styleOverrides: {
          root: ({ theme }) => ({
            "& .MuiTableCell-head": {
              backgroundColor: dark ? "#171f32" : "#eef0f6",
              color: theme.palette.text.secondary,
              fontWeight: 600,
              fontSize: "0.75rem",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              position: "relative",
            },
          }),
        },
      },
      MuiTableRow: {
        styleOverrides: {
          root: ({ theme }) => ({
            "&:hover": {
              backgroundColor: `${alpha(theme.palette.primary.main, dark ? 0.08 : 0.06)} !important`,
            },
          }),
        },
      },
      MuiTableCell: {
        styleOverrides: {
          root: ({ theme }) => ({
            borderColor: theme.palette.divider,
          }),
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: "none",
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: {
            textTransform: "none",
            fontWeight: 600,
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: {
            fontWeight: 600,
          },
        },
      },
    },
  });

  const { palette } = base;
  const dashboard: DashboardPalette = {
    residency: {
      machines: dark ? palette.primary.main : palette.primary.dark,
      private: dark ? palette.primary.light : palette.primary.main,
      external: dark ? palette.secondary.main : palette.secondary.dark,
      unknown: dark ? grey[500] : grey[600],
    },
    health: {
      ok: palette.success.main,
      degraded: dark ? palette.warning.main : "#b26a00",
      error: palette.error.main,
      maintenance: dark ? "#29b6f6" : "#0288d1",
      offline: dark ? grey[600] : grey[700],
      stale: dark ? grey[500] : grey[600],
    },
    load: [0.18, 0.38, 0.58, 0.8, 1].map((a) => alpha(palette.primary.main, a)) as DashboardPalette["load"],
    surfaceSubtle: alpha(dark ? palette.common.white : palette.common.black, 0.03),
  };

  return createTheme(base, { palette: { dashboard } });
}
