/**
 * How the gateway reaches this cluster's models: over mutual TLS through the
 * head's model-TLS proxy, or still over plain HTTP.
 *
 * A cluster started before model TLS keeps its Serve listening on the network
 * until Serve starts again; moving it restarts the models, so it is a button
 * with a confirmation in the card, not something that happens on its own.
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

import { inferenceApi, type ModelTlsStatus } from "~/api/inference";

interface ModelTrafficCardProps {
  environmentId: string;
  /** Serve was shut down: the deployments are being applied again. */
  onMoved?: () => void;
}

export default function ModelTrafficCard({ environmentId, onMoved }: ModelTrafficCardProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<ModelTlsStatus | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [moving, setMoving] = useState(false);
  const [moved, setMoved] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await inferenceApi.modelTls(environmentId));
    } catch {
      setStatus(null); // an older backend: say nothing rather than something wrong
    }
  }, [environmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function move() {
    setMoving(true);
    setError(null);
    try {
      const result = await inferenceApi.moveBehindModelTls(environmentId);
      setMoved(result.moved);
      setConfirming(false);
      onMoved?.();
      void load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.action_failed"));
    } finally {
      setMoving(false);
    }
  }

  if (status === null || !status.enabled) return null;
  const plain = status.plain.length;
  const protectedCount = status.protected.length;
  if (plain === 0 && protectedCount === 0 && moved === null) return null;

  return (
    <Card variant="outlined" data-testid="model-traffic">
      <CardContent>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "flex-start" }}>
          <Box sx={{ flexGrow: 1 }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
              <Typography variant="subtitle2">{t("clusters.model_tls.title")}</Typography>
              {plain === 0 ? (
                <Chip size="small" color="success" icon={<LockIcon />} label={t("clusters.model_tls.encrypted")} />
              ) : (
                <Chip size="small" color="warning" icon={<LockOpenIcon />} label={t("clusters.model_tls.plain", { count: plain })} />
              )}
            </Stack>
            <Typography variant="body2" color="text.secondary">
              {plain === 0 ? t("clusters.model_tls.encrypted_detail") : t("clusters.model_tls.plain_detail")}
            </Typography>
          </Box>
          {plain > 0 && status.can_move && !confirming && (
            <Button size="small" variant="outlined" onClick={() => setConfirming(true)} sx={{ flexShrink: 0 }}>
              {t("clusters.model_tls.move")}
            </Button>
          )}
        </Stack>

        {plain > 0 && !status.head_supported && (
          <Alert severity="info" sx={{ mt: 2 }}>
            {t("clusters.model_tls.update_agent")}
          </Alert>
        )}
        {confirming && (
          <Alert
            severity="warning"
            sx={{ mt: 2 }}
            action={
              <Stack direction="row" spacing={1}>
                <Button size="small" color="inherit" onClick={() => setConfirming(false)} disabled={moving}>
                  {t("common.cancel")}
                </Button>
                <Button
                  size="small"
                  color="warning"
                  variant="contained"
                  onClick={move}
                  disabled={moving}
                  startIcon={moving ? <CircularProgress size={14} /> : undefined}
                >
                  {t("clusters.model_tls.move_confirm")}
                </Button>
              </Stack>
            }
          >
            {t("clusters.model_tls.move_warning")}
          </Alert>
        )}
        {moved !== null && (
          <Alert severity="success" sx={{ mt: 2 }}>
            {t("clusters.model_tls.moved", { count: moved })}
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
