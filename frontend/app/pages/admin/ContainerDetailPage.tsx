/**
 * Admin → Container Detail page.
 * Shows inspect data and live logs.
 */
import { useState, useEffect, useMemo } from "react";
import { useNavigate, useOutletContext, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  containers,
  canEdit,
  isSystemClass,
  type ContainerDetail,
} from "~/api/admin";
import { ClassChip } from "~/components/Chips";
import LogViewer, { fromText } from "~/components/logs/LogViewer";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Grid from "@mui/material/Grid";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";

import EditIcon from "@mui/icons-material/Edit";
import RefreshIcon from "@mui/icons-material/Refresh";

export default function ContainerDetailPage() {
  const { t } = useTranslation();
  const { rootModeActive } = useOutletContext<{ rootModeActive: boolean }>() ?? { rootModeActive: false };
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ContainerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [logs, setLogs] = useState<string>("");
  const [logError, setLogError] = useState<string | null>(null);
  const [logLoading, setLogLoading] = useState(false);
  const logLines = useMemo(() => fromText(logs), [logs]);

  async function load() {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      setDetail(await containers.get(id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("container_detail.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  async function fetchLogs() {
    if (!id) return;
    setLogLoading(true);
    setLogError(null);
    try {
      const res = await containers.fetchLogs(id, 300);
      setLogs(await res.text());
    } catch (e: unknown) {
      setLogs("");
      setLogError(`${t("container_detail.error_loading_logs")}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLogLoading(false);
    }
  }

  useEffect(() => {
    if (tab === 1) fetchLogs();
  }, [tab]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!detail) return null;

  const cls = detail.container_class;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Box>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Typography variant="h5">{detail.name}</Typography>
              <ClassChip value={cls} />
            </Stack>
            <Typography variant="caption" color="text.secondary" fontFamily="monospace">
              {detail.id.slice(0, 64)}
            </Typography>
          </Box>
          {detail.policy !== "locked" && (
            // Fadenstack's own containers are edited only in Root Mode: the button says so.
            <Tooltip
              title={
                canEdit(cls, detail.policy, rootModeActive)
                  ? ""
                  : t("container_detail.edit_needs_root")
              }
            >
              <span>
                <Button
                  variant="outlined"
                  startIcon={<EditIcon />}
                  disabled={!canEdit(cls, detail.policy, rootModeActive)}
                  onClick={() => navigate(`/admin/containers/${detail.id}/edit`)}
                >
                  {t("container_detail.edit")}
                </Button>
              </span>
            </Tooltip>
          )}
        </Stack>

        {/* Tabs */}
        <Paper variant="outlined" sx={{ mb: 2 }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label={t("container_detail.tabs.overview")} />
            <Tab label={t("container_detail.tabs.logs")} />
          </Tabs>
        </Paper>
      </Box>

      {/* Scrollable content area */}
      <Box sx={{ flexGrow: 1, overflow: "auto" }}>
        {/* Overview */}
        {tab === 0 && (
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, md: 6 }}>
              <Paper variant="outlined" sx={{ p: 3 }}>
                <Typography variant="overline" color="text.secondary" gutterBottom>
                  {t("container_detail.status")}
                </Typography>
                <Divider sx={{ mb: 2 }} />
                {[
                  [t("containers.state"), detail.state],
                  [t("containers.image"), detail.image],
                  [t("common.created"), new Date(detail.created).toLocaleString()],
                  [t("create_container.policy"), detail.policy],
                  [t("containers.scope"), detail.owner_scope],
                ].map(([label, value]) => (
                  <Stack
                    key={label}
                    direction="row"
                    justifyContent="space-between"
                    sx={{ py: 0.5 }}
                  >
                    <Typography variant="body2" color="text.secondary">
                      {label}
                    </Typography>
                    <Typography
                      variant="body2"
                      fontFamily={label === t("containers.image") ? "monospace" : undefined}
                      fontSize={label === t("containers.image") ? "0.8rem" : undefined}
                    >
                      {value}
                    </Typography>
                  </Stack>
                ))}
              </Paper>
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <Paper variant="outlined" sx={{ p: 3 }}>
                <Typography variant="overline" color="text.secondary" gutterBottom>
                  {t("networks.title")}
                </Typography>
                <Divider sx={{ mb: 2 }} />
                {detail.networks.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    {t("common.none")}
                  </Typography>
                ) : (
                  <Stack spacing={0.5}>
                    {detail.networks.map((n) => (
                      <Typography key={n} variant="body2" fontFamily="monospace" fontSize="0.8rem">
                        {n}
                      </Typography>
                    ))}
                  </Stack>
                )}
              </Paper>
            </Grid>
          </Grid>
        )}

        {/* Logs */}
        {tab === 1 && (
          <Box>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{ mb: 1 }}
            >
              <Typography variant="caption" color="text.secondary">
                {t("container_detail.last_lines")}
              </Typography>
              <Button
                size="small"
                variant="outlined"
                startIcon={<RefreshIcon />}
                onClick={fetchLogs}
                disabled={logLoading}
              >
                {logLoading ? t("common.loading") : t("dashboard.refresh")}
              </Button>
            </Stack>
            {logError && (
              <Alert severity="error" sx={{ mb: 1 }}>
                {logError}
              </Alert>
            )}
            <LogViewer
              lines={logLines}
              loading={logLoading}
              empty={t("container_detail.no_logs")}
              maxHeight={420}
              downloadName={`${detail.name}.log`}
            />
          </Box>
        )}
      </Box>
    </Box>
  );
}
