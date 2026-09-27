import { createContext, useContext } from "react";
import type { PaletteMode } from "@mui/material";
import { ACCENT_KEYS, ACCENTS, DEFAULT_ACCENT, type Accent } from "./theme";

/** Where the browser keeps the viewer's choices (per browser, like the language). */
export const MODE_STORAGE_KEY = "faden-theme-mode";
export const ACCENT_STORAGE_KEY = "faden-accent";

export interface ThemeModeContextValue {
  mode: PaletteMode;
  toggleMode: () => void;
  setMode: (mode: PaletteMode) => void;
  accent: Accent;
  setAccent: (accent: Accent) => void;
}

export const ThemeModeContext = createContext<ThemeModeContextValue>({
  mode: "dark",
  toggleMode: () => {},
  setMode: () => {},
  accent: DEFAULT_ACCENT,
  setAccent: () => {},
});

export function useThemeMode(): ThemeModeContextValue {
  return useContext(ThemeModeContext);
}

/**
 * Runs in the page's <head>, before the first paint: reads the stored mode
 * and accent onto <html data-mode data-accent>, so the server-rendered page
 * (which does not know them) is painted in the right colours -- see
 * PREPAINT_CSS -- and the app starts from them instead of flashing the
 * defaults. Plain ES5 in a string: it runs before any bundle.
 */
export const PREPAINT_SCRIPT = `(function(){try{var d=document.documentElement,s=window.localStorage,m=s.getItem("${MODE_STORAGE_KEY}"),a=s.getItem("${ACCENT_STORAGE_KEY}");if(m!=="light"&&m!=="dark"){m=window.matchMedia&&window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}if(${JSON.stringify(ACCENT_KEYS)}.indexOf(a)<0){a="${DEFAULT_ACCENT}"}d.dataset.mode=m;d.dataset.accent=a;d.style.colorScheme=m}catch(e){}})();`;

/**
 * What the server-rendered page shows before the app takes over -- the
 * background and the loading spinner -- in the stored mode and accent. The
 * values are the theme's (background.default, primary.main); once the app
 * renders, the theme draws the same colours.
 */
export const PREPAINT_CSS = [
  `html[data-mode="light"] body{background-color:#f5f7fb;color:#1a1f2f}`,
  `html[data-mode="dark"] body{background-color:#0a0e1a;color:#e0e0e0}`,
  ...ACCENT_KEYS.flatMap((accent) =>
    (["dark", "light"] as const).map(
      (mode) =>
        `html[data-accent="${accent}"][data-mode="${mode}"] [data-testid="app-loading"] .MuiCircularProgress-root{color:${ACCENTS[accent].primary[mode].main}}`,
    ),
  ),
].join("");
