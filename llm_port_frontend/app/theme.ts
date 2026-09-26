import { alpha, createTheme } from "@mui/material/styles";
import type { PaletteMode } from "@mui/material";
import { grey } from "@mui/material/colors";

/**
 * The console's theme: a mode (dark / light) and an accent (Nord / Aurora),
 * from the Fadenstack tokens (brand/tokens/fadenstack-tokens.json).
 *
 * The accent gives `primary`, `secondary`, the residency colours and the
 * navigation highlight. It never changes the mark (plates and thread are
 * `palette.brand`), the status colours (`error`, `warning`, `success`,
 * `dashboard.health`) or the backgrounds.
 *
 * Contrast, WCAG 2.x, checked 2026-09-27 for both accents in both modes
 * (paper: #111827 dark, #FFFFFF light):
 *
 *                  main/paper  text on main  nav text  secondary  accent text  residency (machines/private/external/unknown)
 *   Nord dark        8.14        7.72          4.95      6.52       10.44        8.14 / 10.44 / 6.52 / 3.67
 *   Nord light       4.95        4.95          6.75      4.81        6.75        9.65 /  4.95 / 4.81 / 4.83
 *   Aurora dark      3.69        4.81          9.34      7.44        6.14        3.69 /  6.14 / 7.44 / 3.67
 *   Aurora light     4.81        4.81          9.34      4.16        9.34        9.34 /  4.81 / 4.16 / 4.83
 *
 * Every colour a chart, a bar or an outline draws on paper is at least 3:1,
 * and every text on an accent fill at least 4.5:1. The token ramp is used as
 * given on dark; on light a colour under 3:1 on white moves one step darker
 * on its own ramp (Nord's cyan #22C1D3 is 2.2:1 on white, so light Nord is
 * #0E7C86 with white text). The darker teals #0B6570 and #084C55 are the
 * Nord ramp of the docs site. The load scale is the exception by design --
 * a lightly used GPU is meant to look faint -- and is always shown with its
 * numbers. `warning.main` on white is 2.4:1, so the light mode's
 * `dashboard.health.degraded` takes a darker amber (4.24:1).
 */
export type Accent = "nord" | "aurora";

export const ACCENT_KEYS: readonly Accent[] = ["nord", "aurora"];
export const DEFAULT_ACCENT: Accent = "nord";

interface Ramp {
  main: string;
  light: string;
  dark: string;
}

export interface AccentDefinition {
  label: string;
  /** The accent's own colour, for its swatch in Settings. */
  swatch: string;
  primary: Record<PaletteMode, Ramp & { contrastText: string }>;
  secondary: Record<PaletteMode, Ramp>;
  residency: Record<PaletteMode, DashboardPalette["residency"]>;
}

export const ACCENTS: Record<Accent, AccentDefinition> = {
  nord: {
    label: "Nord",
    swatch: "#22C1D3",
    primary: {
      dark: { main: "#22C1D3", light: "#7DD3E8", dark: "#0E7C86", contrastText: "#06202A" },
      light: { main: "#0E7C86", light: "#22C1D3", dark: "#0B6570", contrastText: "#FFFFFF" },
    },
    secondary: {
      dark: { main: "#A78BFA", light: "#C4B5FD", dark: "#7C4DFF" },
      light: { main: "#7C4DFF", light: "#A78BFA", dark: "#3F1DCB" },
    },
    residency: {
      dark: { machines: "#22C1D3", private: "#7DD3E8", external: "#A78BFA", unknown: "#6B7280" },
      light: { machines: "#084C55", private: "#0E7C86", external: "#7C4DFF", unknown: "#6B7280" },
    },
  },
  aurora: {
    label: "Aurora",
    swatch: "#7C4DFF",
    primary: {
      dark: { main: "#7C4DFF", light: "#B47CFF", dark: "#3F1DCB", contrastText: "#FFFFFF" },
      light: { main: "#7C4DFF", light: "#B47CFF", dark: "#3F1DCB", contrastText: "#FFFFFF" },
    },
    secondary: {
      dark: { main: "#00B8D4", light: "#6EFFFF", dark: "#0088A3" },
      light: { main: "#0088A3", light: "#00B8D4", dark: "#0B6570" },
    },
    residency: {
      dark: { machines: "#7C4DFF", private: "#B47CFF", external: "#00B8D4", unknown: "#6B7280" },
      light: { machines: "#3F1DCB", private: "#7C4DFF", external: "#0088A3", unknown: "#6B7280" },
    },
  },
};

export function isAccent(value: unknown): value is Accent {
  return value === "nord" || value === "aurora";
}

/** The mark's colours per mode (brand tokens). Never follows the accent. */
export interface BrandPalette {
  /** The top and bottom plates, and the wordmark. */
  plate: string;
  /** The middle plate: der rote Faden. Never used for text or UI state. */
  thread: string;
}

/** The mark's colours in a mode, from the brand tokens. */
export function brandPalette(mode: PaletteMode): BrandPalette {
  return mode === "dark"
    ? { plate: "#F3F4F6", thread: "#FF4D6D" }
    : { plate: "#0A0E1A", thread: "#E2325A" };
}

/** The accent where it is not a fill: text, the current page, tints. */
export interface AccentPalette {
  /** The accent as text or an icon on paper (at least 4.5:1). */
  text: string;
  /** The current page in a navigation list, and its text. */
  navActiveBg: string;
  navActiveText: string;
  /** Hover and drop-target tints. */
  hover: string;
  tint: string;
}

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
    brand: BrandPalette;
    accent: AccentPalette;
  }
  interface PaletteOptions {
    dashboard?: DashboardPalette;
    brand?: BrandPalette;
    accent?: AccentPalette;
  }
}

export function getAppTheme(mode: PaletteMode, accent: Accent = DEFAULT_ACCENT) {
  const dark = mode === "dark";
  const tokens = ACCENTS[accent] ?? ACCENTS[DEFAULT_ACCENT];

  const base = createTheme({
    palette: {
      mode,
      primary: { ...tokens.primary[mode] },
      secondary: { ...tokens.secondary[mode] },
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
    residency: { ...tokens.residency[mode] },
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

  const brand = brandPalette(mode);
  const accentPalette: AccentPalette = {
    text: dark ? palette.primary.light : palette.primary.dark,
    navActiveBg: palette.primary.dark,
    navActiveText: "#FFFFFF",
    hover: alpha(palette.primary.main, 0.12),
    tint: alpha(palette.primary.main, 0.06),
  };

  return createTheme(base, { palette: { dashboard, brand, accent: accentPalette } });
}
