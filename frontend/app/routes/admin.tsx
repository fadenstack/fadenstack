/**
 * Admin layout — wraps all /admin/* routes with a collapsible sidebar,
 * top bar, root-mode controls, and help wizard.
 *
 * On a phone the sidebar is an overlay opened from a floating button in the
 * bottom corner of the user's hand (or from the logo in the top bar).
 */
import { useState, useEffect } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { accessKeys, adminUsers, rootMode, type MeAccess, type RootModeStatus } from "~/api/admin";
import { auth } from "~/api/auth";
import { useThemeMode } from "~/theme-mode";
import { listLanguages, type UiLanguage } from "~/api/i18n";
import { ServicesProvider, useServices } from "~/lib/ServicesContext";
import { useRagMode } from "~/lib/useRagMode";
import { useTranslation } from "react-i18next";
import i18n from "~/i18n";

import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import { useNavOrder } from "~/lib/useNavOrder";
import { useNavHand } from "~/lib/useNavHand";
import {
  NAV,
  ALL_NAV_IDS,
  DEFAULT_PINNED_IDS,
  adminPageTitle,
  readCachedAccess,
  readCachedAccessAnyAge,
  writeCachedAccess,
  clearCachedAccess,
  consolePages,
  featureShown,
  mayOpen,
  type NavEntry,
} from "~/lib/adminConstants";
import { aboutInfo } from "~/api/about";
import { useAsyncData } from "~/lib/useAsyncData";
import { AdminTopbar } from "~/components/AdminTopbar";
import { ADMIN_NAV_ID, AdminSidebar } from "~/components/AdminSidebar";
import { MobileNavButton } from "~/components/MobileNavButton";
import { RootModeDialog } from "~/components/RootModeDialog";
import { UpdateBanner } from "~/components/UpdateBanner";
import { AdminNotices } from "~/components/AdminNotices";
import { PageHelpDrawer } from "~/components/PageHelpDrawer";
import { GuideRunner } from "~/components/guides/GuideRunner";
import { HowToDialog } from "~/components/guides/HowToDialog";
import { GettingStartedDialog } from "~/components/guides/GettingStarted";
import { GuidesContext, announceGuidesChanged, type GuidesControl } from "~/lib/guidesContext";
import { guideProgressKeys } from "~/lib/guides";
import { preferences as preferencesApi } from "~/api/preferences";
import { applyUserLanguage } from "~/lib/userLanguage";
import { setDashboardQueryOwner } from "~/lib/dashboardQuery";

let meAccessInFlight: ReturnType<typeof adminUsers.meAccess> | null = null;

function fetchMeAccessDedup(): ReturnType<typeof adminUsers.meAccess> {
  if (meAccessInFlight) return meAccessInFlight;
  meAccessInFlight = adminUsers.meAccess().finally(() => {
    meAccessInFlight = null;
  });
  return meAccessInFlight;
}

export default function AdminLayout() {
  return (
    <ServicesProvider>
      <AdminLayoutInner />
    </ServicesProvider>
  );
}

function AdminLayoutInner() {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useThemeMode();
  const { t } = useTranslation();
  const [languages, setLanguages] = useState<UiLanguage[]>([]);
  const [language, setLanguage] = useState(
    i18n.resolvedLanguage || i18n.language || "en",
  );
  const [languageMenuAnchor, setLanguageMenuAnchor] =
    useState<HTMLElement | null>(null);
  const [rootStatus, setRootStatus] = useState<RootModeStatus | null>(null);
  const [showRootForm, setShowRootForm] = useState(false);
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState(600);
  const [error, setError] = useState<string | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [currentUserEmail, setCurrentUserEmail] = useState<string>("");
  const [isSuperuser, setIsSuperuser] = useState(false);
  const [permissionKeys, setPermissionKeys] = useState<Set<string>>(new Set());

  /* Guides: the running one, the how-to list, the getting-started list */
  const [activeGuide, setActiveGuide] = useState<string | null>(null);
  const [guideRun, setGuideRun] = useState(0);
  const [showHowTos, setShowHowTos] = useState(false);
  const [showGettingStarted, setShowGettingStarted] = useState(false);
  const guides: GuidesControl = {
    startGuide: (id) => {
      setPageHelpOpen(false);
      setActiveGuide(id);
      // A guide started again while it runs starts over.
      setGuideRun((n) => n + 1);
    },
    openGettingStarted: () => setShowGettingStarted(true),
  };

  /* Page-help drawer (F1 / "About This Page") */
  const [pageHelpOpen, setPageHelpOpen] = useState(false);

  /* Drawer open/collapsed state */
  const [drawerOpen, setDrawerOpen] = useState(true);

  /* Phone navigation: an overlay panel instead of the side rail */
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [navHand, setNavHand] = useNavHand();
  useEffect(() => {
    // Whatever navigated (a link in the panel, the back button, a page's own
    // link), the panel is done.
    setMobileNavOpen(false);
  }, [location.pathname, location.search, isPhone]);

  /* ── DnD: nav reordering ────────────────────────────────────────── */
  const { order, setOrder, resetOrder } = useNavOrder(
    ALL_NAV_IDS,
    DEFAULT_PINNED_IDS,
  );

  function applyAccessState(access: MeAccess) {
    const permissions = accessKeys(access);
    setCurrentUserEmail(access.email);
    setIsSuperuser(access.is_superuser);
    setPermissionKeys(new Set(permissions));
    setAuthReady(true);
    writeCachedAccess({
      email: access.email,
      isSuperuser: access.is_superuser,
      permissions,
    });
  }

  async function ensureAuthenticated() {
    try {
      const access = await fetchMeAccessDedup();
      // Superusers, and those a permission lets onto some console pages (consolePages).
      if (!access.is_superuser && consolePages(false, accessKeys(access))?.length === 0) {
        writeCachedAccess({
          email: access.email,
          isSuperuser: access.is_superuser,
          permissions: accessKeys(access),
        });
        navigate("/chat", { replace: true });
        return;
      }
      applyAccessState(access);
      // The language the user picked in an agent or on another device.
      void applyUserLanguage();
    } catch {
      clearCachedAccess();
      navigate(
        `/login?next=${encodeURIComponent(`${location.pathname}${location.search}`)}`,
        {
          replace: true,
        },
      );
    }
  }

  async function loadRootStatus() {
    try {
      const s = await rootMode.status();
      setRootStatus(s);
    } catch {
      // ignore
    }
  }

  async function loadLanguages() {
    try {
      const supported = await listLanguages();
      setLanguages(supported);
    } catch {
      setLanguages([{ code: "en", name: "English" }]);
    }
  }

  useEffect(() => {
    const freshCachedAccess = readCachedAccess();
    const cachedAccess = freshCachedAccess ?? readCachedAccessAnyAge();
    if (cachedAccess !== null) {
      if (!cachedAccess.isSuperuser && consolePages(false, cachedAccess.permissions)?.length === 0) {
        navigate("/chat", { replace: true });
        return;
      }
      setCurrentUserEmail(cachedAccess.email);
      setIsSuperuser(cachedAccess.isSuperuser);
      setPermissionKeys(new Set(cachedAccess.permissions));
      setAuthReady(true);
    }
    void ensureAuthenticated();
    void loadLanguages();
  }, []);

  useEffect(() => {
    const handler = () =>
      setLanguage(i18n.resolvedLanguage || i18n.language || "en");
    i18n.on("languageChanged", handler);
    return () => {
      i18n.off("languageChanged", handler);
    };
  }, []);

  /* ── F1 keyboard shortcut for page help ─────────────────────────── */
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "F1") {
        e.preventDefault();
        setPageHelpOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    const page = adminPageTitle(location.pathname, location.search, t);
    document.title = `${page} | ${t("app.title")}`;
  }, [location.pathname, location.search, t]);

  useEffect(() => {
    if (!authReady || !isSuperuser) return;
    loadRootStatus();
    const interval = setInterval(loadRootStatus, 15000);
    return () => clearInterval(interval);
  }, [authReady, isSuperuser]);

  /** Forgets which guides were done and brings the getting-started list back. */
  async function handleResetGuides() {
    setActiveGuide(null);
    const progress = await preferencesApi.get().catch(() => null);
    // A key set to null is removed; an empty object would merge into nothing.
    const cleared = Object.fromEntries(
      [...guideProgressKeys(), ...Object.keys(progress?.preferences?.tour_progress ?? {})].map((k) => [k, null]),
    );
    try {
      await preferencesApi.update({ tour_progress: cleared, getting_started: null });
    } catch {
      // best-effort
    }
    announceGuidesChanged();
    if (location.pathname !== "/admin/dashboard") setShowGettingStarted(true);
  }

  async function handleLogout() {
    try {
      await auth.logout();
    } finally {
      clearCachedAccess();
      navigate("/login", { replace: true });
    }
  }

  async function handleActivateRoot(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await rootMode.start(reason, "all", duration);
      setShowRootForm(false);
      setReason("");
      await loadRootStatus();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("common.action_failed"),
      );
    }
  }

  async function handleDeactivateRoot() {
    await rootMode.stop();
    await loadRootStatus();
  }

  // The pages someone who is not a superuser may open; any other console address leads to the first.
  const pages = consolePages(isSuperuser, permissionKeys);
  // Until that redirect has happened, a page they may not open is not rendered at all: it would ask the
  // server for what it shows, and get 403s, before the address changes.
  const pageOpens = pages === null || mayOpen(pages, location.pathname);
  useEffect(() => {
    if (!authReady || pageOpens) return;
    navigate(pages?.[0] ?? "/chat", { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authReady, isSuperuser, permissionKeys, location.pathname]);

  const isRootActive = rootStatus?.active ?? false;
  /** No permission needed, or this one, or (a list) any of them. */
  const hasPermission = (permission?: string | string[]): boolean => {
    if (!permission) return true;
    if (isSuperuser) return true;
    return typeof permission === "string"
      ? permissionKeys.has(permission)
      : permission.some((p) => permissionKeys.has(p));
  };
  const { isModuleEnabled } = useServices();

  // Determine RAG mode for nav filtering
  const { mode: ragMode } = useRagMode();

  // What the server adds beyond core: a page for a feature shows only while it is offered.
  const features = useAsyncData(
    () =>
      aboutInfo()
        .then((about) => new Set(about.features ?? []) as ReadonlySet<string>)
        .catch(() => new Set<string>() as ReadonlySet<string>),
    [],
    { initialValue: new Set<string>() as ReadonlySet<string> },
  );

  // Filter entries by permissions / modules / superuser (and, for anyone else, the pages they may
  // open), then split by saved order
  const opens = (to: string) => mayOpen(pages, to.split("?")[0]);
  const visibleEntries = NAV.map((entry) => {
    if (entry.module && !isModuleEnabled(entry.module)) return null;
    if (entry.kind === "leaf") {
      if (entry.superuserOnly && !isSuperuser) return null;
      if (!opens(entry.to)) return null;
      return hasPermission(entry.permission) ? entry : null;
    }
    const children = entry.children.filter((child) => {
      if (child.module && !isModuleEnabled(child.module)) return false;
      if (!featureShown(child, features.data)) return false;
      if (child.superuserOnly && !isSuperuser) return false;
      if (!opens(child.to)) return false;
      if (!hasPermission(child.permission)) return false;
      if (child.ragMode && ragMode && child.ragMode !== ragMode) return false;
      return true;
    });
    return children.length > 0 ? { ...entry, children } : null;
  }).filter((entry): entry is NavEntry => entry !== null);

  const visibleById = new Map(visibleEntries.map((e) => [e.id, e]));
  const visibleIdSet = new Set(visibleEntries.map((e) => e.id));
  const mainVisible = order.mainIds
    .filter((id) => visibleIdSet.has(id))
    .map((id) => visibleById.get(id)!);
  const pinnedVisible = order.pinnedIds
    .filter((id) => visibleIdSet.has(id))
    .map((id) => visibleById.get(id)!);

  if (!authReady) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
        }}
      >
        <Typography color="text.secondary">{t("app.check_session")}</Typography>
      </Box>
    );
  }

  // The shared query cache is this user's, before anything here reads it: the
  // update notice (banner, sidebar) is in it from the first page on. Set only by
  // the dashboard, it was emptied under the banner on the first visit there.
  setDashboardQueryOwner(currentUserEmail || "local");

  return (
    <GuidesContext.Provider value={guides}>
    <Box
      sx={{
        display: "flex",
        height: "100vh",
        // A phone's 100vh reaches behind the browser's toolbar.
        "@supports (height: 100dvh)": { height: "100dvh" },
        overflow: "hidden",
      }}
    >
      <AdminSidebar
        drawerOpen={drawerOpen}
        setDrawerOpen={setDrawerOpen}
        mainVisible={mainVisible}
        pinnedVisible={pinnedVisible}
        order={order}
        setOrder={setOrder}
        resetOrder={resetOrder}
        mobile={
          isPhone
            ? {
                open: mobileNavOpen,
                onClose: () => setMobileNavOpen(false),
                hand: navHand,
                onHandChange: setNavHand,
              }
            : undefined
        }
      />
      {isPhone && (
        <MobileNavButton
          hand={navHand}
          open={mobileNavOpen}
          onOpen={() => setMobileNavOpen(true)}
          controls={ADMIN_NAV_ID}
        />
      )}

      <Box
        sx={{
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <AdminTopbar
          mode={mode}
          toggleMode={toggleMode}
          currentUserEmail={currentUserEmail}
          isSuperuser={isSuperuser}
          rootStatus={rootStatus}
          languages={languages}
          language={language}
          languageMenuAnchor={languageMenuAnchor}
          onLanguageMenuOpen={(e) => setLanguageMenuAnchor(e.currentTarget)}
          onLanguageMenuClose={() => setLanguageMenuAnchor(null)}
          onLanguageChange={setLanguage}
          onGettingStartedOpen={() => setShowGettingStarted(true)}
          onConsoleTourOpen={() => guides.startGuide("console_tour")}
          onHowToGuidesOpen={() => setShowHowTos(true)}
          onResetGuides={handleResetGuides}
          onPageHelp={() => setPageHelpOpen(true)}
          onRootFormOpen={() => setShowRootForm(true)}
          onRootDeactivate={handleDeactivateRoot}
          onLogout={handleLogout}
          onOpenNav={isPhone ? () => setMobileNavOpen(true) : undefined}
        />

        {/* A critical or security release, or a release past its support. */}
        <UpdateBanner isSuperuser={isSuperuser} pathname={location.pathname} />
        {/* What the backend's notice sources say (Enterprise: its licence). */}
        <AdminNotices isSuperuser={isSuperuser} />

        <RootModeDialog
          open={showRootForm}
          reason={reason}
          duration={duration}
          error={error}
          onReasonChange={setReason}
          onDurationChange={setDuration}
          onSubmit={handleActivateRoot}
          onClose={() => {
            setShowRootForm(false);
            setError(null);
          }}
        />

        <GettingStartedDialog
          open={showGettingStarted}
          onClose={() => setShowGettingStarted(false)}
        />

        <HowToDialog
          open={showHowTos}
          isSuperuser={isSuperuser}
          onClose={() => setShowHowTos(false)}
          onStart={guides.startGuide}
        />

        {activeGuide && (
          <GuideRunner
            key={`${activeGuide}-${guideRun}`}
            guideId={activeGuide}
            isSuperuser={isSuperuser}
            onEnd={() => setActiveGuide(null)}
          />
        )}

        <PageHelpDrawer
          open={pageHelpOpen}
          onClose={() => setPageHelpOpen(false)}
        />

        <Box
          sx={{
            flexGrow: 1,
            minHeight: 0,
            overflow: "auto",
            p: { xs: 2, sm: 3 },
            // Room under the last row for the floating menu button.
            pb: { xs: "calc(96px + env(safe-area-inset-bottom, 0px))", sm: 3 },
            // On a phone the whole page scrolls, under the button. A page that fills
            // the height and scrolls inside itself (settings, audit, roles, ...) ended
            // above that room instead, cut off, with the button in an empty band.
            // A page that needs its height (a graph canvas) says so.
            [theme.breakpoints.down("sm")]: {
              "& > :not([data-keeps-height])": { height: "auto" },
            },
          }}
        >
          {pageOpens && (
            <Outlet
              context={{
                rootModeActive: isRootActive,
                can: hasPermission,
                isSuperuser,
                userEmail: currentUserEmail,
              }}
            />
          )}
        </Box>
      </Box>
    </Box>
    </GuidesContext.Provider>
  );
}
