/**
 * Models a cluster runs that no deployment here owns, and taking them over.
 *
 * A deployment deleted while its cluster could not be reached leaves its model
 * running: it holds GPUs and still answers at the gateway, and nothing else in
 * the console shows it. Asking the cluster takes its head machine some
 * seconds, so the check runs when asked, not on every visit.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import SearchIcon from "@mui/icons-material/Search";

import { inferenceApi, type FoundClusterApp } from "~/api/inference";

interface StrayModelsCardProps {
  environmentId: string;
  /** A model was taken over: it is a deployment of this cluster now. */
  onTakenOver?: () => void;
}

export default function StrayModelsCard({ environmentId, onTakenOver }: StrayModelsCardProps) {
  const { t } = useTranslation();
  const [models, setModels] = useState<FoundClusterApp[] | null>(null);
  const [aliases, setAliases] = useState<Record<string, string>>({});
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taken, setTaken] = useState<string | null>(null);

  const failure = (err: unknown) => (err instanceof Error ? err.message : t("common.action_failed"));

  async function check() {
    setChecking(true);
    setError(null);
    setTaken(null);
    try {
      const found = await inferenceApi.strayModels(environmentId);
      setModels(found.models);
      setAliases(Object.fromEntries(found.models.map((m) => [m.app_name, m.suggested_alias])));
      if (found.error) setError(found.error);
    } catch (err: unknown) {
      setError(failure(err));
    } finally {
      setChecking(false);
    }
  }

  async function takeOver(model: FoundClusterApp) {
    setBusy(model.app_name);
    setError(null);
    try {
      const alias = (aliases[model.app_name] ?? "").trim() || model.suggested_alias;
      const result = await inferenceApi.takeOverStrayModels(environmentId, [model.app_name], {
        [model.app_name]: alias,
      });
      setTaken(result.deployments[0]?.name ?? model.model_id);
      setModels((current) => (current ?? []).filter((m) => m.app_name !== model.app_name));
      onTakenOver?.();
    } catch (err: unknown) {
      setError(failure(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card variant="outlined" data-testid="stray-models">
      <CardContent>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "flex-start" }}>
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="subtitle2" gutterBottom>
              {t("clusters.stray.title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("clusters.stray.description")}
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            onClick={check}
            disabled={checking || busy !== null}
            startIcon={checking ? <CircularProgress size={14} /> : <SearchIcon />}
            sx={{ flexShrink: 0 }}
          >
            {checking ? t("clusters.stray.checking") : t("clusters.stray.check")}
          </Button>
        </Stack>

        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        {taken && (
          <Alert severity="success" sx={{ mt: 2 }}>
            {t("clusters.stray.taken", { name: taken })}
          </Alert>
        )}
        {models && models.length === 0 && !error && !taken && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            {t("clusters.stray.none")}
          </Typography>
        )}

        {models && models.length > 0 && (
          <Table size="small" sx={{ mt: 2 }}>
            <TableHead>
              <TableRow>
                <TableCell>{t("clusters.stray.col_model")}</TableCell>
                <TableCell align="right">{t("clusters.stray.col_running")}</TableCell>
                <TableCell>{t("clusters.stray.col_alias")}</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {models.map((model) => (
                <TableRow key={model.app_name}>
                  <TableCell>
                    <Typography variant="body2">{model.model_id}</Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                      {model.app_name}
                    </Typography>
                    {model.notes.map((note) => (
                      <Typography key={note} variant="caption" color="warning.main" component="div">
                        {note}
                      </Typography>
                    ))}
                  </TableCell>
                  <TableCell align="right">
                    {model.running_copies} / {model.copies}
                  </TableCell>
                  <TableCell>
                    <TextField
                      size="small"
                      value={aliases[model.app_name] ?? ""}
                      onChange={(e) => setAliases((a) => ({ ...a, [model.app_name]: e.target.value }))}
                      inputProps={{ "aria-label": t("clusters.stray.col_alias") }}
                    />
                  </TableCell>
                  <TableCell align="right">
                    <Button
                      size="small"
                      variant="contained"
                      onClick={() => takeOver(model)}
                      disabled={busy !== null || checking}
                      startIcon={busy === model.app_name ? <CircularProgress size={14} /> : undefined}
                    >
                      {t("clusters.stray.take_over")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
