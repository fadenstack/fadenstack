import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
} from "react-router";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import type { PaletteMode } from "@mui/material";
import { alpha, ThemeProvider } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import GlobalStyles from "@mui/material/GlobalStyles";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";
import { DEFAULT_ACCENT, getAppTheme, isAccent, type Accent } from "./theme";
import {
  ACCENT_STORAGE_KEY,
  MODE_STORAGE_KEY,
  PREPAINT_CSS,
  PREPAINT_SCRIPT,
  ThemeModeContext,
} from "./theme-mode";
import i18n from "./i18n";

import type { Route } from "./+types/root";
import "@fontsource/sora/600.css";
import "@fontsource/sora/700.css";
import "./app.css";

export const meta: Route.MetaFunction = () => [
  { title: "Fadenstack" },
  { name: "description", content: "The self-hosted LLM control plane" },
  { name: "application-name", content: "Fadenstack" },
  { name: "apple-mobile-web-app-title", content: "Fadenstack" },
  { name: "theme-color", content: "#0A0E1A" },
];

export const links: Route.LinksFunction = () => [
  { rel: "icon", href: "/favicon.ico", sizes: "any" },
  { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
  { rel: "apple-touch-icon", href: "/apple-touch-icon.png" },
  { rel: "manifest", href: "/manifest.webmanifest" },
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
  {
    rel: "stylesheet",
    href: "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap",
  },
];

// The server has no layout pass; on it this is a no-op effect.
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

function storedMode(): PaletteMode | null {
  const fromHead = document.documentElement.dataset.mode;
  if (fromHead === "light" || fromHead === "dark") return fromHead;
  try {
    const stored = window.localStorage.getItem(MODE_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage blocked: the default stands.
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : null;
}

function storedAccent(): Accent | null {
  const fromHead = document.documentElement.dataset.accent;
  if (isAccent(fromHead)) return fromHead;
  try {
    const stored = window.localStorage.getItem(ACCENT_STORAGE_KEY);
    if (isAccent(stored)) return stored;
  } catch {
    // Storage blocked: the default stands.
  }
  return null;
}

export function Layout({ children }: { children: React.ReactNode }) {
  // The server renders the defaults; the browser takes the stored choices
  // before its first paint (PREPAINT_SCRIPT put them on <html> already).
  const [mode, setMode] = useState<PaletteMode>("dark");
  const [accent, setAccent] = useState<Accent>(DEFAULT_ACCENT);

  useIsomorphicLayoutEffect(() => {
    const m = storedMode();
    if (m) setMode(m);
    const a = storedAccent();
    if (a) setAccent(a);
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(MODE_STORAGE_KEY, mode);
      window.localStorage.setItem(ACCENT_STORAGE_KEY, accent);
    } catch {
      // Storage blocked: the choice lasts for this page only.
    }
    const root = document.documentElement;
    root.dataset.mode = mode;
    root.dataset.accent = accent;
    root.style.colorScheme = mode;
    document.body.dataset.theme = mode;
  }, [mode, accent]);

  const theme = useMemo(() => getAppTheme(mode, accent), [mode, accent]);
  const themeModeValue = useMemo(
    () => ({
      mode,
      toggleMode: () => setMode((prev) => (prev === "dark" ? "light" : "dark")),
      setMode,
      accent,
      setAccent,
    }),
    [mode, accent],
  );

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <script dangerouslySetInnerHTML={{ __html: PREPAINT_SCRIPT }} />
        <style dangerouslySetInnerHTML={{ __html: PREPAINT_CSS }} />
        <Meta />
        <Links />
      </head>
      <body>
        <ThemeModeContext.Provider value={themeModeValue}>
          <ThemeProvider theme={theme}>
            <CssBaseline />
            <GlobalStyles
              styles={{
                // The scrollbars take the accent (app.css reads these).
                body: {
                  "--scroll-thumb": alpha(theme.palette.primary.main, mode === "dark" ? 0.25 : 0.3),
                  "--scroll-thumb-hover": alpha(theme.palette.primary.main, mode === "dark" ? 0.45 : 0.5),
                },
              }}
            />
            {children}
          </ThemeProvider>
        </ThemeModeContext.Provider>
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

/**
 * Whether the translations are in (``i18n.ts`` loads them from /api/i18n).
 *
 * Only the browser can load them: the server has no backend to ask. It
 * rendered every page with raw keys ("auth.login_title") where the browser
 * had text, so hydration failed on every page load and React threw the
 * server's tree away. Starting from ``false`` keeps the server render and the
 * hydration render identical; the pages render once the translations are in.
 */
function useTranslationsReady(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (i18n.isInitialized) {
      setReady(true);
      return;
    }
    const done = () => setReady(true);
    i18n.on("initialized", done);
    return () => i18n.off("initialized", done);
  }, []);
  return ready;
}

export default function App() {
  const ready = useTranslationsReady();
  if (!ready) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", pt: 12 }} data-testid="app-loading">
        <CircularProgress />
      </Box>
    );
  }
  return <Outlet />;
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const { t } = useTranslation();
  let message = t("errors.oops");
  let details = t("errors.unexpected");
  let stack: string | undefined;

  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "404" : "Error";
    details =
      error.status === 404
        ? t("errors.not_found")
        : error.statusText || details;
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message;
    stack = error.stack;
  }

  return (
    <Box sx={{ pt: 8, p: 4, maxWidth: 960, mx: "auto" }}>
      <Typography variant="h4" gutterBottom>
        {message}
      </Typography>
      <Typography color="text.secondary">{details}</Typography>
      {stack && (
        <Box
          component="pre"
          sx={{
            mt: 2,
            p: 2,
            overflow: "auto",
            bgcolor: "background.paper",
            borderRadius: 1,
            fontSize: "0.75rem",
          }}
        >
          <code>{stack}</code>
        </Box>
      )}
    </Box>
  );
}
