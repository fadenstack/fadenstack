/**
 * ContainerLogsDialog — full-width overlay that shows container logs
 * for a module.  Each container gets its own tab so the admin can
 * switch between them without closing the dialog.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CloseIcon from "@mui/icons-material/Close";
import RefreshIcon from "@mui/icons-material/Refresh";

import { servicesApi, type ContainerState } from "~/api/services";
import LogViewer, { fromText } from "~/components/logs/LogViewer";

// ── Types ────────────────────────────────────────────────────────────

export interface ContainerLogsDialogProps {
  open: boolean;
  onClose: () => void;
  /** Module name (e.g. "rag", "pii", "mailer"). */
  module: string;
  /** Human-readable module name for the dialog title. */
  moduleDisplayName: string;
  /** Containers belonging to this module. */
  containers: ContainerState[];
}

const TAIL_OPTIONS = [50, 100, 200, 500, 1000];

// ── Per-tab log panel (internal) ─────────────────────────────────────

function LogPanel({
  module,
  containerName,
  active,
}: {
  module: string;
  containerName: string;
  /** Only fetch / render when the tab is active. */
  active: boolean;
}) {
  const { t } = useTranslation();
  const [logs, setLogs] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tail, setTail] = useState(200);
  const lines = useMemo(() => fromText(logs), [logs]);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setLogs(await servicesApi.containerLogs(module, containerName, tail));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.load_failed"));
      setLogs("");
    } finally {
      setLoading(false);
    }
  }, [module, containerName, tail]);

  // Fetch when the panel becomes active or tail changes.
  useEffect(() => {
    if (active) {
      void fetchLogs();
    }
  }, [active, fetchLogs]);

  if (!active) return null;

  return (
    <Box sx={{ minHeight: 0, flexGrow: 1 }}>
      <LogViewer
        lines={lines}
        loading={loading}
        empty={error ?? t("logs.empty", { defaultValue: "No log output." })}
        maxHeight="calc(80vh - 170px)"
        downloadName={`${containerName}.log`}
        toolbar={
          <>
            <TextField
              select
              size="small"
              variant="standard"
              value={tail}
              onChange={(e) => setTail(Number(e.target.value))}
              sx={{ minWidth: 80 }}
              slotProps={{ inputLabel: { shrink: true } }}
              label={t("logs.tail_label", { defaultValue: "Lines" })}
            >
              {TAIL_OPTIONS.map((n) => (
                <MenuItem key={n} value={n}>
                  {n}
                </MenuItem>
              ))}
            </TextField>
            <Tooltip title={t("dashboard.refresh", { defaultValue: "Refresh" })}>
              <span>
                <IconButton size="small" onClick={() => void fetchLogs()} disabled={loading}>
                  <RefreshIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </>
        }
      />
    </Box>
  );
}

// ── Dialog ───────────────────────────────────────────────────────────

export default function ContainerLogsDialog({
  open,
  onClose,
  module,
  moduleDisplayName,
  containers,
}: ContainerLogsDialogProps) {
  const { t } = useTranslation();
  const [tabIndex, setTabIndex] = useState(0);

  // Reset to first tab when the dialog opens for a different module.
  useEffect(() => {
    if (open) setTabIndex(0);
  }, [open, module]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{
        sx: { height: "80vh", display: "flex", flexDirection: "column" },
      }}
    >
      <DialogTitle sx={{ display: "flex", alignItems: "center", pb: 0 }}>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          {t("modules_tab.logs_title", {
            defaultValue: "{{module}} — Container Logs",
            module: moduleDisplayName,
          })}
        </Typography>
        <IconButton edge="end" onClick={onClose} aria-label="close">
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      {containers.length > 1 && (
        <Tabs
          value={tabIndex}
          onChange={(_e, v: number) => setTabIndex(v)}
          variant="scrollable"
          scrollButtons="auto"
          sx={{ px: 3 }}
        >
          {containers.map((c) => (
            <Tab key={c.name} label={c.name} />
          ))}
        </Tabs>
      )}

      <DialogContent
        sx={{
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          pt: containers.length > 1 ? 1 : 2,
        }}
      >
        {containers.map((c, idx) => (
          <LogPanel
            key={c.name}
            module={module}
            containerName={c.name}
            active={idx === tabIndex}
          />
        ))}
      </DialogContent>
    </Dialog>
  );
}
