/**
 * DrainPlanDialog — what draining (or removing) a machine would do, before it is done.
 *
 * Asks the server for the plan when it opens: which of the machine's model
 * copies can move to the cluster's other machines and where, and which have
 * nowhere to go and would stop -- with the reason. Nothing stops unless the
 * operator confirms knowing it (``stopUnmovable``).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ReportProblemOutlinedIcon from "@mui/icons-material/ReportProblemOutlined";

import type { DrainPlan } from "~/api/inference";

export interface DrainPlanDialogProps {
  open: boolean;
  /** Drain the machine, or take it out of its cluster (drained first). */
  mode: "drain" | "remove";
  /** The machine's name, for the title while the plan loads. */
  machine: string;
  loadPlan: () => Promise<DrainPlan>;
  onConfirm: (stopUnmovable: boolean) => Promise<void>;
  onClose: () => void;
}

export function DrainPlanDialog({ open, mode, machine, loadPlan, onConfirm, onClose }: DrainPlanDialogProps) {
  const { t } = useTranslation();
  const [plan, setPlan] = useState<DrainPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPlan(null);
    setError(null);
    loadPlan()
      .then((p) => !cancelled && setPlan(p))
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const stopping = plan?.stopping ?? 0;
  const confirmLabel =
    mode === "remove"
      ? stopping
        ? t("clusters.drain.remove_and_stop", { count: stopping })
        : t("clusters.drain.remove")
      : stopping
        ? t("clusters.drain.drain_and_stop", { count: stopping })
        : t("clusters.drain.drain");

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(stopping > 0);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const running = plan?.cluster_running && plan.ray_node_id;

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {mode === "remove"
          ? t("clusters.drain.remove_title", { name: plan?.machine ?? machine })
          : t("clusters.drain.title", { name: plan?.machine ?? machine })}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          {!plan && !error && (
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 2 }} data-drain-planning="">
              <CircularProgress size={20} />
              <Typography variant="body2" color="text.secondary">
                {t("clusters.drain.planning")}
              </Typography>
            </Stack>
          )}
          {plan && !plan.can_drain && <Alert severity="warning">{plan.reason}</Alert>}
          {plan && plan.can_drain && (
            <>
              <Typography variant="body2">
                {!plan.in_cluster
                  ? t("clusters.drain.not_in_cluster", { name: plan.machine })
                  : !running
                    ? mode === "remove"
                      ? t("clusters.drain.remove_stopped", { name: plan.machine, cluster: plan.environment_name })
                      : t("clusters.drain.cluster_stopped", { name: plan.machine })
                    : plan.copies.length === 0
                      ? mode === "remove"
                        ? t("clusters.drain.remove_empty", { name: plan.machine })
                        : t("clusters.drain.empty", { name: plan.machine })
                      : mode === "remove"
                        ? t("clusters.drain.remove_intro", { name: plan.machine })
                        : t("clusters.drain.intro", { name: plan.machine })}
              </Typography>
              {plan.copies.length > 0 && (
                <Stack spacing={1} data-drain-copies="">
                  {plan.copies.map((copy, i) => (
                    <Stack
                      key={copy.replica_id ?? `${copy.app}-${i}`}
                      direction="row"
                      spacing={1.5}
                      alignItems="flex-start"
                      sx={{ border: 1, borderColor: "divider", borderRadius: 1, px: 1.5, py: 1 }}
                      data-drain-copy={copy.moves ? "moves" : "stops"}
                    >
                      {copy.moves ? (
                        <CheckCircleOutlineIcon color="success" fontSize="small" sx={{ mt: 0.25 }} />
                      ) : (
                        <ReportProblemOutlinedIcon color="warning" fontSize="small" sx={{ mt: 0.25 }} />
                      )}
                      <Box sx={{ minWidth: 0 }}>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {copy.name}{" "}
                          <Typography component="span" variant="caption" color="text.secondary">
                            {t("clusters.drain.copy_gpus", { gpus: copy.gpus, machines: copy.spans.join(", ") })}
                          </Typography>
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          {copy.moves
                            ? t("clusters.drain.moves_to", { machines: copy.to.join(", ") })
                            : t("clusters.drain.stops", { reason: copy.reason ?? "" })}
                        </Typography>
                      </Box>
                    </Stack>
                  ))}
                </Stack>
              )}
              {running && plan.copies.some((c) => c.moves) && (
                <Typography variant="caption" color="text.secondary">
                  {t("clusters.drain.keeps_serving")}
                </Typography>
              )}
              {stopping > 0 && (
                <Alert severity="warning">{t("clusters.drain.stopping_warning", { count: stopping })}</Alert>
              )}
              {running && mode === "drain" && (
                <Typography variant="caption" color="text.secondary">
                  {t("clusters.drain.how_to_undo")}
                </Typography>
              )}
              {plan.note && (
                <Typography variant="caption" color="text.secondary">
                  {plan.note}
                </Typography>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        {plan?.can_drain && (
          <Button
            variant="contained"
            color={stopping > 0 || mode === "remove" ? "warning" : "primary"}
            disabled={busy}
            onClick={() => void confirm()}
            data-drain-confirm=""
          >
            {busy ? <CircularProgress size={18} color="inherit" /> : confirmLabel}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
