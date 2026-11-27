/**
 * "Serve models on this Mac": the one click that puts a Mac in its own
 * standalone environment (one machine, one model at a time, no cluster) and
 * opens it, beside the Mac's status.
 */
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { inferenceApi } from "~/api/inference";

import { MacStatusCard } from "./MacStatusCard";

export function MacServeSection({
  nodeId,
  capabilities,
}: {
  nodeId: string;
  capabilities: Record<string, unknown>;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function serve() {
    setBusy(true);
    setError(null);
    try {
      const env = await inferenceApi.createStandaloneEnvironment(nodeId);
      navigate(`/admin/clusters/${env.id}`);
    } catch (e) {
      setError(t("mac.serve_failed", { error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack spacing={1.5} data-testid="mac-serve">
      <MacStatusCard capabilities={capabilities} />
      <Stack direction="row" spacing={2} alignItems="center">
        <Button variant="contained" disabled={busy} onClick={() => void serve()} data-testid="mac-serve-button">
          {t("mac.serve_button")}
        </Button>
        <Typography variant="body2" color="text.secondary">
          {t("mac.serve_hint")}
        </Typography>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
    </Stack>
  );
}
