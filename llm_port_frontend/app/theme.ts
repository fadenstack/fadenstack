import { alpha, createTheme } from "@mui/material/styles";
import type { PaletteMode } from "@mui/material";
import { grey } from "@mui/material/colors";

/**
 * The console's theme: a mode (dark / light) and an accent (Frost, Graphite
 * or Aurora).
 *
 * Aurora is the Fadenstack tokens' violet (brand/tokens/fadenstack-tokens.json).
 * Frost and Graphite replaced the tokens' Nord cyan on 2026-09-27, by the
 * owner's choice: the cyan was the loudest colour on the page (8.1:1 on the
 * dark card, against 3.5 for the healthy green and 4.2 for the error red)
 * and sat next to green, so it drew the eye from the statuses. Frost is a
 * muted steel blue (Nord's frost #81A1C1, about half the cyan's saturation);
 * Graphite has no hue at all, so colour means status only. A stored "nord"
 * is no longer an accent and falls back to the default, Frost.
 *
 * The accent gives `primary`, `secondary`, the residency colours and the
 * navigation highlight. It never changes the mark (plates and thread are
 * `palette.brand`), the status colours (`error`, `warning`, `success`,
 * `dashboard.health`) or the backgrounds.
 *
 * Contrast, WCAG 2.x, checked 2026-09-27 (paper: #111827 dark, #FFFFFF light).
 * "Text on hover" is the button's text on `primary.dark`, which MUI fills a
 * contained button with on hover.
 *
 *                  main/   text on  text on  nav    secondary  accent  residency
 *                  paper   main     hover    text              text    (machines/private/external/unknown)
 *   Frost dark     6.59    7.15     5.71     5.55   6.26        9.48    6.59 / 10.35 / 6.26 / 3.67
 *   Frost light    5.55    5.55     7.71     7.71   5.43        7.71    7.71 /  3.07 / 5.43 / 4.83
 *   Graphite dark 14.33   14.33    12.04    10.31   6.99       14.33   14.33 /  6.99 / 6.59 / 3.67
 *   Graphite light14.68   14.68    17.74    14.68   4.83       14.68   14.68 /  7.56 / 5.55 / 4.83
 *   Aurora dark    3.69    4.81     9.34     9.34   7.44        6.14    3.69 /  6.14 / 7.44 / 3.67
 *   Aurora light   4.81    4.81     9.34     9.34   4.16        9.34    9.34 /  4.81 / 4.16 / 4.83
 *
 * Every colour a chart, a bar or an outline draws on paper is at least 3:1,
 * and every text on an accent fill -- hovered or not, and the current page --
 * at least 4.5:1 (theme.test.ts holds this). On light, a colour under 3:1 on
 * white moves one step darker on its own ramp. The load scale is the
 * exception by design -- a lightly used GPU is meant to look faint -- and is
 * always shown with its numbers. `warning.main` on white is 2.4:1, so the
 * light mode's `dashboard.health.degraded` takes a darker amber (4.24:1).
 */
export type Accent = "frost" | "graphite" | "aurora";

export const ACCENT_KEYS: readonly Accent[] = ["frost", "graphite", "aurora"];
export const DEFAULT_ACCENT: Accent = "frost";

interface Ramp {
  main: string;
  light: string;
  dark: string;
}

export interface AccentDefinition {
  label: string;
  primary: Record<PaletteMode, Ramp & { contrastText: string }>;
  secondary: Record<PaletteMode, Ramp>;
  residency: Record<PaletteMode, DashboardPalette["residency"]>;
  /** The current page in a navigation list: its fill and text. */
  nav: Record<PaletteMode, { bg: string; text: string }>;
  /** The accent as text or an icon on paper. */
  text: Record<PaletteMode, string>;
}

export const ACCENTS: Record<Accent, AccentDefinition> = {
  frost: {
    label: "Frost",
    primary: {
      dark: { main: "#81A1C1", light: "#A9C0D8", dark: "#6F8FB0", contrastText: "#0A0E1A" },
      light: { main: "#4C6A92", light: "#81A1C1", dark: "#3B5478", contrastText: "#FFFFFF" },
    },
    secondary: {
      dark: { main: "#B48EAD", light: "#CDB3C8", dark: "#8A5A83" },
      light: { main: "#8A5A83", light: "#B48EAD", dark: "#6E4467" },
    },
    residency: {
      dark: { machines: "#81A1C1", private: "#B4C8DE", external: "#B48EAD", unknown: "#6B7280" },
      light: { machines: "#3B5478", private: "#7C95B8", external: "#8A5A83", unknown: "#6B7280" },
    },
    nav: {
      dark: { bg: "#4C6A92", text: "#FFFFFF" },
      light: { bg: "#3B5478", text: "#FFFFFF" },
    },
    text: { dark: "#A9C0D8", light: "#3B5478" },
  },
  graphite: {
    label: "Graphite",
    primary: {
      dark: { main: "#E5E7EB", light: "#F3F4F6", dark: "#D1D5DB", contrastText: "#111827" },
      light: { main: "#1F2937", light: "#4B5563", dark: "#111827", contrastText: "#FFFFFF" },
    },
    secondary: {
      dark: { main: "#9CA3AF", light: "#D1D5DB", dark: "#6B7280" },
      light: { main: "#6B7280", light: "#9CA3AF", dark: "#4B5563" },
    },
    residency: {
      dark: { machines: "#E5E7EB", private: "#9CA3AF", external: "#81A1C1", unknown: "#6B7280" },
      light: { machines: "#1F2937", private: "#4B5563", external: "#4C6A92", unknown: "#6B7280" },
    },
    nav: {
      dark: { bg: "#374151", text: "#FFFFFF" },
      light: { bg: "#1F2937", text: "#FFFFFF" },
    },
    text: { dark: "#E5E7EB", light: "#1F2937" },
  },
  aurora: {
    label: "Aurora",
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
    nav: {
      dark: { bg: "#3F1DCB", text: "#FFFFFF" },
      light: { bg: "#3F1DCB", text: "#FFFFFF" },
    },
    text: { dark: "#B47CFF", light: "#3F1DCB" },
  },
};

export function isAccent(value: unknown): value is Accent {
  return typeof value === "string" && (ACCENT_KEYS as readonly string[]).includes(value);
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
    text: tokens.text[mode],
    navActiveBg: tokens.nav[mode].bg,
    navActiveText: tokens.nav[mode].text,
    hover: alpha(palette.primary.main, 0.12),
    tint: alpha(palette.primary.main, 0.06),
  };

  return createTheme(base, { palette: { dashboard, brand, accent: accentPalette } });
}
