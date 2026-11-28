/**
 * "Serve models on this Mac": the one click that puts a Mac in its own
 * standalone environment (one machine, one model at a time, no cluster) and
 * opens it, beside the Mac's status.
 */
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { useCan } from "~/lib/useCan";

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
  const can = useCan();
  // The server refuses the rest; this only keeps a button off a Mac that cannot serve.
  const mayServe =
    can("inference.environments:create") &&
    can("inference.environments:update") &&
    Number(capabilities.model_tls_native ?? 0) > 0;
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
    <Stack spacing={1} data-testid="mac-serve">
      <MacStatusCard
        capabilities={capabilities}
        action={
          mayServe ? (
            <Tooltip title={t("mac.serve_hint")} arrow>
              <span>
                <Button
                  size="small"
                  variant="contained"
                  disabled={busy}
                  onClick={() => void serve()}
                  data-testid="mac-serve-button"
                >
                  {t("mac.serve_button")}
                </Button>
              </span>
            </Tooltip>
          ) : undefined
        }
      />
      {error && <Alert severity="error">{error}</Alert>}
    </Stack>
  );
}
