/**
 * Admin dashboard: an overview of the whole platform, built from widgets the
 * user arranges, shows, hides and resizes (spec:
 * llm-port-dev/dashboard/dashboard-redesign-spec.md).
 *
 * The page is only a frame. Each widget owns its data -- it loads, refreshes
 * and fails on its own -- so one slow or broken source never holds up the
 * rest; the old page waited ~27 s because every card shared one request.
 */
import { Component, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useOutletContext } from "react-router";
import { useTranslation } from "react-i18next";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Snackbar from "@mui/material/Snackbar";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import TuneIcon from "@mui/icons-material/Tune";
import { preferences } from "~/api/preferences";
import CustomizeDialog from "~/components/dashboard/CustomizeDialog";
import DashboardGrid, { TOUCH_TARGET, type GridItem } from "~/components/dashboard/DashboardGrid";
import { OVERVIEW_REFRESH_MS, useAttention, useOverview } from "~/components/dashboard/queries";
import RefreshRing from "~/components/dashboard/RefreshRing";
import { WIDGETS } from "~/components/dashboard/registry";
import type { WidgetDefinition } from "~/components/dashboard/types";
import WidgetCard from "~/components/dashboard/WidgetCard";
import {
  moveWidget,
  reorder,
  setSize,
  sizeOf,
  toggleHidden,
  type Profile,
} from "~/lib/dashboardLayoutModel";
import { setDashboardQueryOwner } from "~/lib/dashboardQuery";
import { useServices } from "~/lib/ServicesContext";
import { useRagMode } from "~/lib/useRagMode";
import { useDashboardLayout } from "~/lib/useDashboardLayout";

interface AdminOutletContext {
  isSuperuser?: boolean;
  userEmail?: string;
}

export default function DashboardPage() {
  const context = useOutletContext<AdminOutletContext | undefined>();
  const navigate = useNavigate();
  // Outside the admin layout (a test rendering the page alone) nothing is known
  // about the user; the server still refuses non-superusers.
  const isSuperuser = context?.isSuperuser ?? true;
  const userKey = context?.userEmail || "local";
  // Before any widget reads it: a different user must not see cached figures.
  setDashboardQueryOwner(userKey);
  const profile = useProfile();

  useEffect(() => {
    // Every dashboard endpoint is superuser-only; /admin sends others to /chat.
    if (!isSuperuser) navigate("/admin", { replace: true });
  }, [isSuperuser, navigate]);

  if (!isSuperuser) return null;
  if (profile === null) return <PageSkeleton />;
  return <Dashboard userKey={userKey} profile={profile} />;
}

/** The onboarding profile, which picks the default layout; "team" when unknown. */
function useProfile(): Profile | null {
  const [profile, setProfile] = useState<Profile | null>(null);
  useEffect(() => {
    let cancelled = false;
    preferences
      .get()
      .then((res) => !cancelled && setProfile(res.preferences.profile ?? "team"))
      .catch(() => !cancelled && setProfile("team"));
    return () => {
      cancelled = true;
    };
  }, []);
  return profile;
}

function Dashboard({ userKey, profile }: { userKey: string; profile: Profile }) {
  const { t } = useTranslation();
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const { isModuleEnabled, loading: servicesLoading } = useServices();
  const { mode: ragMode } = useRagMode();
  const { layout, update, reset, conflict, keepMine, dismissConflict, saveError } = useDashboardLayout(WIDGETS, {
    userKey,
    profile,
  });
  const [editMode, setEditMode] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const overview = useOverview({ refreshToken });
  // Shared with the Needs attention widget: fetched once.
  const attention = useAttention({ refreshToken });

  const items: GridItem[] = useMemo(() => {
    if (!layout) return [];
    const byId = new Map(WIDGETS.map((w) => [w.id, w]));
    return layout.order.flatMap((id) => {
      const def = byId.get(id);
      if (!def) return [];
      const reason = unavailableReason(def, { servicesLoading, isModuleEnabled, ragMode, t });
      return [
        {
          def,
          size: sizeOf(layout, def),
          hidden: layout.hidden.includes(id),
          available: reason === null,
          reason: reason ?? undefined,
        },
      ];
    });
  }, [layout, servicesLoading, isModuleEnabled, ragMode, t]);

  const available = items.filter((i) => i.available).map((i) => i.def.id);
  const specFor = (id: string) => WIDGETS.find((w) => w.id === id);
  const ops = {
    onReorder: (a: string, b: string) => update((l) => reorder(l, a, b)),
    onMove: (id: string, delta: -1 | 1) => update((l) => moveWidget(l, id, delta, available)),
    onToggle: (id: string) => update((l) => toggleHidden(l, id)),
    onSize: (id: string, size: GridItem["size"]) => {
      const spec = specFor(id);
      if (spec) update((l) => setSize(l, spec, size));
    },
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pb: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "flex-start" }} gap={1.5}>
        <Box>
          <Typography variant="h5" component="h1">
            {t("dashboard.title")}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {t("dashboard.subtitle")}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
          <SystemPill
            status={overview.data?.system_status}
            badge={overview.data?.system_badge}
            critical={attention.data?.counts.critical}
            warning={attention.data?.counts.warning}
          />
          {isPhone ? (
            <>
              <Tooltip title={t("dashboard.customize")}>
                <IconButton onClick={() => setEditMode(true)} aria-label={t("dashboard.customize")} sx={TOUCH_TARGET}>
                  <TuneIcon />
                </IconButton>
              </Tooltip>
            </>
          ) : (
            <>
              {editMode && (
                <Button startIcon={<RestartAltIcon />} onClick={reset}>
                  {t("dashboard.reset_layout")}
                </Button>
              )}
              <Button
                variant={editMode ? "contained" : "outlined"}
                startIcon={<TuneIcon />}
                onClick={() => setEditMode((v) => !v)}
                aria-pressed={editMode}
              >
                {editMode ? t("dashboard.done") : t("dashboard.customize")}
              </Button>
            </>
          )}
          <RefreshRing
            updatedAt={overview.updatedAt}
            intervalMs={OVERVIEW_REFRESH_MS}
            refreshing={overview.fetching}
            failed={Boolean(overview.error)}
            onRefresh={() => setRefreshToken((n) => n + 1)}
          />
        </Stack>
      </Stack>

      {layout === null ? (
        <GridSkeleton />
      ) : (
        <DashboardGrid
          items={items}
          editMode={editMode && !isPhone}
          {...ops}
          renderWidget={(item, fill) => (
            <WidgetBoundary title={t(item.def.titleKey)}>
              <Suspense fallback={<WidgetCard title={t(item.def.titleKey)} status={{ loading: true }} />}>
                <item.def.component
                  size={item.size}
                  layout={layout}
                  onLayoutChange={(patch) => update((l) => ({ ...l, ...patch }))}
                  refreshToken={refreshToken}
                  fill={fill}
                />
              </Suspense>
            </WidgetBoundary>
          )}
        />
      )}

      {isPhone && (
        <CustomizeDialog
          open={editMode}
          items={items}
          onClose={() => setEditMode(false)}
          onReset={reset}
          onMove={ops.onMove}
          onToggle={ops.onToggle}
          onSize={ops.onSize}
        />
      )}

      <Snackbar
        open={conflict !== null}
        message={t("dashboard.layout_conflict")}
        onClose={(_, reason) => reason !== "clickaway" && dismissConflict()}
        action={
          <>
            <Button color="inherit" size="small" onClick={keepMine} sx={{ minHeight: 36 }}>
              {t("dashboard.keep_mine")}
            </Button>
            <Button color="inherit" size="small" onClick={dismissConflict} sx={{ minHeight: 36 }}>
              {t("dashboard.keep_theirs")}
            </Button>
          </>
        }
      />
      {saveError && !conflict && (
        <Alert severity="warning" variant="outlined">
          {t("dashboard.layout_save_failed")}
        </Alert>
      )}
    </Box>
  );
}

function unavailableReason(
  def: WidgetDefinition,
  ctx: {
    servicesLoading: boolean;
    isModuleEnabled: (name: string) => boolean;
    ragMode: string | null;
    t: (key: string, opts?: Record<string, unknown>) => string;
  },
): string | null {
  const module = def.requires?.module;
  if (module) {
    if (ctx.servicesLoading) return ctx.t("dashboard.checking_modules");
    if (!ctx.isModuleEnabled(module)) {
      return ctx.t("dashboard.requires_module", {
        module: ctx.t(`modules.${module}.name`, { defaultValue: module }),
      });
    }
  }
  if (def.requires?.ragPro && ctx.ragMode !== "pro") return ctx.t("dashboard.requires_rag_pro");
  return null;
}

/**
 * Overall state: the attention list's worst severity, or the overview's own
 * verdict when nothing needs attention. The word is always there, not just a
 * colour. (The overview alone only knows this host and its database; with the
 * clusters down it would still say "healthy".)
 */
function SystemPill({
  status,
  badge,
  critical,
  warning,
}: {
  status?: string;
  badge?: string;
  critical?: number;
  warning?: number;
}) {
  const { t } = useTranslation();
  const theme = useTheme();
  if (!status && critical === undefined) return <Skeleton variant="rounded" width={140} height={28} />;
  const tone = critical ? "error" : warning ? "degraded" : badge === "red" ? "error" : badge === "yellow" ? "degraded" : "ok";
  const label = critical
    ? t("dashboard.system_state.critical", { count: critical })
    : warning
      ? t("dashboard.system_state.warning", { count: warning })
      : t(`dashboard.system_state.${tone}`);
  const color = theme.palette.dashboard.health[tone];
  return (
    <Box
      role="status"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.75,
        px: 1.25,
        py: 0.5,
        borderRadius: 999,
        border: 1,
        borderColor: color,
        color: "text.primary",
        typography: "caption",
        fontWeight: 700,
      }}
    >
      <Box component="span" aria-hidden sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: color }} />
      {label}
    </Box>
  );
}

function PageSkeleton() {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Skeleton variant="text" width={220} height={40} />
      <GridSkeleton />
    </Box>
  );
}

function GridSkeleton() {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 1.5 }}>
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} variant="rounded" height={220} />
      ))}
    </Box>
  );
}

/** A widget that throws while rendering takes only itself down. */
class WidgetBoundary extends Component<{ title: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <WidgetCard
          title={this.props.title}
          status={{ error: "render", onRetry: () => this.setState({ failed: false }) }}
        />
      );
    }
    return this.props.children;
  }
}
