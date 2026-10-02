/**
 * Where a cluster's machines get their models: copied from this server (the
 * default, which works with no internet on the machines), or downloaded from
 * Hugging Face by each machine itself.
 *
 * The switch turns on only when every machine answers that it reaches
 * Hugging Face; until then it stays off, and its tooltip names the machines
 * that cannot and why. The server refuses it too, so this is not the only
 * guard. Nothing restarts: it applies to the next model placed on the cluster.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CloudDoneIcon from "@mui/icons-material/CloudDone";
import CloudOffIcon from "@mui/icons-material/CloudOff";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";

import { inferenceApi, type MachineInternet, type ModelDownloadsStatus } from "~/api/inference";

interface ModelDownloadsCardProps {
  environmentId: string;
}

export default function ModelDownloadsCard({ environmentId }: ModelDownloadsCardProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<ModelDownloadsStatus | null>(null);
  const [checking, setChecking] = useState(true);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  const load = useCallback(
    async (fresh = false) => {
      setChecking(true);
      try {
        setStatus(await inferenceApi.modelDownloads(environmentId, fresh));
      } catch {
        setMissing(true); // an older backend: say nothing rather than something wrong
      } finally {
        setChecking(false);
      }
    },
    [environmentId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  async function flip(on: boolean) {
    setSwitching(true);
    setError(null);
    try {
      setStatus(await inferenceApi.switchModelDownloads(environmentId, on));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.action_failed"));
      void load(true);
    } finally {
      setSwitching(false);
    }
  }

  if (missing || (status !== null && status.machines.length === 0)) return null;

  const on = status?.on ?? false;
  // Off can always be chosen; on only when every machine can do it.
  const blocked = !on && status !== null && !status.can_switch_on;
  const lacking = (status?.machines ?? []).filter((m) => m.internet !== true);
  const tooltip = blocked ? (
    <Box>
      <Typography variant="body2" sx={{ mb: 0.5 }}>
        {t("clusters.model_downloads.cannot_switch_on")}
      </Typography>
      {lacking.map((m) => (
        <Typography key={m.node_id} variant="body2" component="div">
          • {t(`clusters.model_downloads.problem.${m.problem ?? "no_answer"}`, { host: m.host })}
        </Typography>
      ))}
    </Box>
  ) : (
    ""
  );

  return (
    <Card variant="outlined" data-testid="model-downloads">
      <CardContent>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "flex-start" }}>
          <Box sx={{ flexGrow: 1 }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
              <Typography variant="subtitle2">{t("clusters.model_downloads.title")}</Typography>
              {status && (
                <Chip
                  size="small"
                  color={on ? "success" : "default"}
                  label={on ? t("clusters.model_downloads.on_chip") : t("clusters.model_downloads.off_chip")}
                />
              )}
            </Stack>
            <Typography variant="body2" color="text.secondary">
              {on ? t("clusters.model_downloads.on_detail") : t("clusters.model_downloads.off_detail")}
            </Typography>
          </Box>
          <Tooltip title={tooltip} placement="left" describeChild>
            {/* A disabled switch fires no events: the span carries the tooltip. */}
            <span data-testid="model-downloads-switch">
              <FormControlLabel
                sx={{ mr: 0, flexShrink: 0 }}
                control={
                  <Switch
                    checked={on}
                    disabled={status === null || switching || (checking && !on) || blocked}
                    onChange={(_event, checked) => void flip(checked)}
                    inputProps={{ "aria-label": t("clusters.model_downloads.switch_label") }}
                  />
                }
                label={t("clusters.model_downloads.switch_label")}
                labelPlacement="start"
              />
            </span>
          </Tooltip>
        </Stack>

        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1.5, flexWrap: "wrap", rowGap: 1 }}>
          {checking && !status && (
            <Stack direction="row" spacing={1} alignItems="center">
              <CircularProgress size={14} />
              <Typography variant="body2" color="text.secondary">
                {t("clusters.model_downloads.checking")}
              </Typography>
            </Stack>
          )}
          {status?.machines.map((m) => <MachineChip key={m.node_id} machine={m} />)}
          {status && (
            <Button
              size="small"
              onClick={() => void load(true)}
              disabled={checking}
              startIcon={checking ? <CircularProgress size={12} /> : undefined}
            >
              {t("clusters.model_downloads.check_again")}
            </Button>
          )}
        </Stack>
        {on && (
          <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
            {t("clusters.model_downloads.applies_next")}
          </Typography>
        )}
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}

function MachineChip({ machine }: { machine: MachineInternet }) {
  const { t } = useTranslation();
  const icon =
    machine.internet === true ? (
      <CloudDoneIcon />
    ) : machine.internet === false ? (
      <CloudOffIcon />
    ) : (
      <HelpOutlineIcon />
    );
  const label =
    machine.internet === true
      ? `${machine.host}: ${t("clusters.model_downloads.reaches")}`
      : t(`clusters.model_downloads.problem.${machine.problem ?? "no_answer"}`, { host: machine.host });
  return (
    <Tooltip title={machine.detail ?? ""}>
      <Chip
        size="small"
        variant="outlined"
        icon={icon}
        color={machine.internet === true ? "success" : machine.internet === false ? "error" : "warning"}
        label={label}
      />
    </Tooltip>
  );
}
