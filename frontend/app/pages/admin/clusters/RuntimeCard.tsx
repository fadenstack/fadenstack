/**
 * The runtime a cluster's machines run -- one image with Ray and vLLM -- and
 * the others this server ships for them. A newer certified runtime is only
 * announced; switching restarts the cluster and its models, so it is a
 * button with a confirmation (why: services/inference/runtime_choice.py).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { inferenceApi, type ClusterRuntime, type ClusterRuntimeStatus } from "~/api/inference";
import { useAsyncData } from "~/lib/useAsyncData";
import RestartConfirm from "./RestartConfirm";

interface RuntimeCardProps {
  environmentId: string;
  /** The cluster was switched: it restarts. */
  onSwitched?: () => void;
}

export default function RuntimeCard({ environmentId, onSwitched }: RuntimeCardProps) {
  const { t } = useTranslation();
  const [restarting, setRestarting] = useState(false);
  // An older backend has no runtime choice: the data stays null and the card says nothing.
  const { data: status, refresh } = useAsyncData<ClusterRuntimeStatus | null>(
    () => inferenceApi.runtime(environmentId),
    [environmentId],
    { initialValue: null, refreshMs: restarting ? 5000 : undefined },
  );
  const [picked, setPicked] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Look again while the cluster restarts, and only then: a stopped cluster
  // takes the switch when it starts, and waits without being asked.
  useEffect(() => setRestarting(Boolean(status?.restarting)), [status]);

  async function switchTo(runtime: string) {
    setSwitching(true);
    setError(null);
    try {
      await inferenceApi.switchRuntime(environmentId, runtime);
      setConfirming(false);
      setPicked("");
      onSwitched?.();
      await refresh(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.action_failed"));
    } finally {
      setSwitching(false);
    }
  }

  if (!status || status.runtimes.length === 0) return null;

  const running = status.runtimes.filter((r) => status.runs.includes(r.bundle_id));
  const update = status.runtimes.find((r) => r.bundle_id === status.update);
  const target = status.runtimes.find((r) => r.bundle_id === picked);

  return (
    <Card variant="outlined" data-testid="cluster-runtime">
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
          <Typography variant="subtitle2">{t("clusters.runtime.title")}</Typography>
          {status.restarting && (
            <Chip size="small" color="info" icon={<CircularProgress size={12} />} label={t("clusters.runtime.applying")} />
          )}
          {!status.applied && !status.restarting && (
            <Chip size="small" variant="outlined" label={t("clusters.applies_on_start")} />
          )}
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {t("clusters.runtime.detail")}
        </Typography>
        {running.map((runtime) => (
          <Stack key={runtime.bundle_id} direction="row" spacing={1} alignItems="center" sx={{ flexWrap: "wrap", rowGap: 0.5 }}>
            <Typography variant="body2">
              {t("clusters.runtime.stack", { ray: runtime.runtime_version, vllm: runtime.vllm_version })}
            </Typography>
            <RuntimeChips runtime={runtime} />
            <Typography variant="caption" color="text.secondary" fontFamily="monospace">
              {runtime.image}
            </Typography>
          </Stack>
        ))}
        {/* A runtime this server no longer ships: shown by its id, with the move offered below. */}
        {(status.withdrawn ?? []).map((id) => (
          <Typography key={id} variant="body2" fontFamily="monospace" data-testid="runtime-withdrawn">
            {id}
          </Typography>
        ))}

        {update && !confirming && (
          <Alert
            severity={status.withdrawn?.length ? "warning" : "info"}
            sx={{ mt: 2 }}
            data-testid="runtime-update"
            action={
              <Button size="small" color="inherit" onClick={() => { setPicked(update.bundle_id); setConfirming(true); }}>
                {t("clusters.runtime.switch")}
              </Button>
            }
          >
            {status.withdrawn?.length
              ? t("clusters.runtime.withdrawn", { vllm: update.vllm_version })
              : t("clusters.runtime.update", { vllm: update.vllm_version })}
          </Alert>
        )}

        {status.runtimes.length > 1 && !confirming && (
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} sx={{ mt: 2 }}>
            <TextField
              select
              size="small"
              label={t("clusters.runtime.choose")}
              value={picked}
              onChange={(event) => setPicked(event.target.value)}
              sx={{ minWidth: 280 }}
            >
              {status.runtimes.map((runtime) => (
                <MenuItem
                  key={runtime.bundle_id}
                  value={runtime.bundle_id}
                  disabled={runtime.certification_status === "failed" || status.runs.includes(runtime.bundle_id)}
                >
                  <Stack direction="row" spacing={1} alignItems="center">
                    <span>{t("clusters.runtime.option", { vllm: runtime.vllm_version, ray: runtime.runtime_version })}</span>
                    <RuntimeChips runtime={runtime} />
                  </Stack>
                </MenuItem>
              ))}
            </TextField>
            <Button size="small" variant="outlined" disabled={!picked} onClick={() => setConfirming(true)}>
              {t("clusters.runtime.switch")}
            </Button>
          </Stack>
        )}

        {confirming && target && (
          <RestartConfirm
            confirmLabel={t("clusters.runtime.restart_confirm")}
            busy={switching}
            onConfirm={() => void switchTo(target.bundle_id)}
            onCancel={() => setConfirming(false)}
          >
            <Box>
              {t("clusters.runtime.restart_warning", { vllm: target.vllm_version })}
              {target.channel === "preview" && (
                <Typography variant="body2" sx={{ mt: 0.5 }}>
                  {t("clusters.runtime.preview_hint")}
                </Typography>
              )}
            </Box>
          </RestartConfirm>
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

function RuntimeChips({ runtime }: { runtime: ClusterRuntime }) {
  const { t } = useTranslation();
  const status = runtime.certification_status;
  const color = status === "passed" ? "success" : status === "failed" ? "error" : "warning";
  return (
    <>
      <Chip size="small" variant="outlined" color={color} label={t(`clusters.runtime.status.${status}`)} />
      {runtime.channel === "preview" && <Chip size="small" color="secondary" label={t("clusters.runtime.preview")} />}
      {runtime.default && <Chip size="small" variant="outlined" label={t("clusters.runtime.recommended")} />}
    </>
  );
}
