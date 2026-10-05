/**
 * AddMachinesDialog — add machines to a cluster that already runs.
 *
 * Lists every machine that is not in the cluster: the ones that can join to
 * tick, the others with the reason they cannot. The server checks each one
 * against the head on the cluster's network before anything changes; the
 * reconciler then brings it up the way the cluster came up.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { inferenceApi, type MemberCandidates } from "~/api/inference";

export interface AddMachinesDialogProps {
  open: boolean;
  clusterId: string;
  clusterName: string;
  onClose: () => void;
  onAdded: () => void;
}

export function AddMachinesDialog({ open, clusterId, clusterName, onClose, onAdded }: AddMachinesDialogProps) {
  const { t } = useTranslation();
  const [candidates, setCandidates] = useState<MemberCandidates | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setCandidates(null);
    setPicked([]);
    setError(null);
    inferenceApi
      .listMemberCandidates(clusterId)
      .then((c) => !cancelled && setCandidates(c))
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [open, clusterId]);

  async function add() {
    setBusy(true);
    setError(null);
    try {
      await inferenceApi.addMembers(clusterId, picked);
      onAdded();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const machines = candidates?.machines ?? [];
  const toggle = (id: string) =>
    setPicked((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]));

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("clusters.add_machines.title", { name: clusterName })}</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">
            {t("clusters.add_machines.intro")}
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
          {!candidates && !error && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <CircularProgress size={24} />
            </Box>
          )}
          {candidates && machines.length === 0 && (
            <Alert severity="info">{t("clusters.add_machines.none")}</Alert>
          )}
          <Stack spacing={1}>
            {machines.map((m) => (
              <Stack
                key={m.node_id}
                direction="row"
                spacing={1}
                alignItems="flex-start"
                sx={{ border: 1, borderColor: "divider", borderRadius: 1, px: 1, py: 0.75, opacity: m.can_add ? 1 : 0.7 }}
                data-candidate={m.node_id}
              >
                <Checkbox
                  size="small"
                  checked={picked.includes(m.node_id)}
                  disabled={!m.can_add || busy}
                  onChange={() => toggle(m.node_id)}
                  inputProps={{ "aria-label": m.machine }}
                  sx={{ mt: -0.5 }}
                />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {m.machine}{" "}
                    <Typography component="span" variant="caption" color="text.secondary">
                      {t("clusters.add_machines.machine_line", { host: m.host, count: m.gpus })}
                    </Typography>
                  </Typography>
                  {m.reasons.map((reason) => (
                    <Typography key={reason} variant="caption" color="text.secondary" display="block">
                      {reason}
                    </Typography>
                  ))}
                  {m.warnings.map((warning) => (
                    <Typography key={warning} variant="caption" color="warning.main" display="block">
                      {warning}
                    </Typography>
                  ))}
                </Box>
              </Stack>
            ))}
          </Stack>
          {candidates?.network && (
            <Typography variant="caption" color="text.secondary">
              {t("clusters.add_machines.checked", { network: candidates.network })}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        <Button variant="contained" disabled={busy || picked.length === 0} onClick={() => void add()} data-add-machines="">
          {busy ? <CircularProgress size={18} color="inherit" /> : t("clusters.add_machines.add", { count: picked.length })}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
