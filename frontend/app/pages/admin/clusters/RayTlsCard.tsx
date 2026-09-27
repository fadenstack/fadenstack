/**
 * Ray's own traffic between the cluster's machines -- object transfers, the
 * GCS, the raylets -- over mutual TLS with the cluster's CA, or in the clear.
 *
 * Ray reads its certificates only when it starts, so switching restarts the
 * cluster and its models: a button with a confirmation, like moving model
 * traffic behind TLS.
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
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import LockIcon from "@mui/icons-material/Lock";
import LockOpenIcon from "@mui/icons-material/LockOpen";

import { inferenceApi, type RayTlsStatus } from "~/api/inference";

interface RayTlsCardProps {
  environmentId: string;
  /** The switch was flipped: the cluster restarts. */
  onSwitched?: () => void;
}

export default function RayTlsCard({ environmentId, onSwitched }: RayTlsCardProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<RayTlsStatus | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await inferenceApi.rayTls(environmentId));
    } catch {
      setStatus(null); // an older backend: say nothing rather than something wrong
    }
  }, [environmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  // While the cluster restarts on the new setting, look again now and then.
  useEffect(() => {
    if (!status || status.applied) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [status, load]);

  async function flip() {
    if (!status) return;
    setSwitching(true);
    setError(null);
    try {
      setStatus(await inferenceApi.switchRayTls(environmentId, !status.on));
      setConfirming(false);
      onSwitched?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.action_failed"));
    } finally {
      setSwitching(false);
    }
  }

  // One machine: Ray's traffic never leaves it. Still shown when on, so it can be switched off.
  if (status === null || (status.machines < 2 && !status.on)) return null;

  const chip = !status.applied ? (
    <Chip size="small" color="info" icon={<CircularProgress size={12} />} label={t("clusters.ray_tls.applying")} />
  ) : status.on ? (
    <Chip size="small" color="success" icon={<LockIcon />} label={t("clusters.ray_tls.encrypted")} />
  ) : (
    <Chip size="small" color="warning" icon={<LockOpenIcon />} label={t("clusters.ray_tls.plain")} />
  );
  const canFlip = status.on || status.can_switch_on;

  return (
    <Card variant="outlined" data-testid="ray-tls">
      <CardContent>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "flex-start" }}>
          <Box sx={{ flexGrow: 1 }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
              <Typography variant="subtitle2">{t("clusters.ray_tls.title")}</Typography>
              {chip}
            </Stack>
            <Typography variant="body2" color="text.secondary">
              {status.on ? t("clusters.ray_tls.encrypted_detail") : t("clusters.ray_tls.plain_detail")}
            </Typography>
          </Box>
          {canFlip && !confirming && (
            <Button size="small" variant="outlined" onClick={() => setConfirming(true)} sx={{ flexShrink: 0 }}>
              {status.on ? t("clusters.ray_tls.switch_off") : t("clusters.ray_tls.switch_on")}
            </Button>
          )}
        </Stack>

        {!status.on && status.old_agents.length > 0 && (
          <Alert severity="info" sx={{ mt: 2 }}>
            {t("clusters.ray_tls.update_agents", { hosts: status.old_agents.join(", ") })}
          </Alert>
        )}
        {confirming && (
          <Alert
            severity="warning"
            sx={{ mt: 2 }}
            action={
              <Stack direction="row" spacing={1}>
                <Button size="small" color="inherit" onClick={() => setConfirming(false)} disabled={switching}>
                  {t("common.cancel")}
                </Button>
                <Button
                  size="small"
                  color="warning"
                  variant="contained"
                  onClick={flip}
                  disabled={switching}
                  startIcon={switching ? <CircularProgress size={14} /> : undefined}
                >
                  {t("clusters.ray_tls.restart_confirm")}
                </Button>
              </Stack>
            }
          >
            {t("clusters.ray_tls.restart_warning")}
          </Alert>
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
