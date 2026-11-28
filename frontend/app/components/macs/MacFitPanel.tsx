/**
 * The numbers behind a Mac's memory plan, as the backend computed them: what
 * the GPU can use, what is kept for the system, what the model and its
 * conversation cache take, and the share vLLM-Metal is told to use. Shown so
 * an operator sees why a model fits or does not.
 */
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

import type { Fit } from "~/api/marketplace";
import { formatBytes } from "~/lib/engine";

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" noWrap component="div" title={label}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        {value}
      </Typography>
    </Box>
  );
}

export function MacFitPanel({ fit }: { fit: Fit | null }) {
  const { t } = useTranslation();
  const metal = fit?.metal;
  if (!fit || !metal) return null;
  const estimated = metal.estimated ? ` (${t("mac.fit_estimated")})` : "";
  if (fit.status === "no_accelerators") {
    return <Alert severity="info">{t("mac.fit_waiting")}</Alert>;
  }
  if (fit.status === "too_large") {
    return (
      <Alert severity="error" data-testid="mac-fit-too-large">
        {t("mac.fit_too_large", {
          needed: formatBytes(fit.needed_bytes_per_gpu),
          available: `${formatBytes(metal.ceiling_bytes)}${estimated}`,
        })}
      </Alert>
    );
  }
  const share = fit.suggested_gpu_memory_utilization;
  return (
    <Paper variant="outlined" sx={{ px: 1.5, py: 1 }} data-testid="mac-fit">
      <Stack direction="row" alignItems="baseline" justifyContent="space-between" spacing={2} sx={{ mb: 0.75 }}>
        <Typography variant="subtitle2">{t("mac.fit_title")}</Typography>
        <Typography variant="caption" color="text.secondary">
          {t("mac.fit_one_model")}
        </Typography>
      </Stack>
      <Box
        sx={{
          display: "grid",
          gap: 1.25,
          columnGap: 2,
          gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", sm: "repeat(4, minmax(0, 1fr))" },
        }}
      >
        <Cell label={t("mac.fit_working_set")} value={`${formatBytes(metal.working_set_bytes)}${estimated}`} />
        <Cell label={t("mac.fit_reserve")} value={formatBytes(metal.reserve_bytes)} />
        <Cell label={t("mac.fit_weights")} value={formatBytes(fit.weights_bytes)} />
        <Cell label={t("mac.fit_cache")} value={formatBytes(metal.kv_bytes)} />
        <Cell label={t("mac.fit_budget")} value={formatBytes(metal.budget_bytes)} />
        {share != null && <Cell label={t("mac.fit_share")} value={`${Math.round(share * 100)} %`} />}
        {fit.context != null && <Cell label={t("mac.fit_context")} value={fit.context.toLocaleString("en-US")} />}
        {fit.max_num_seqs != null && <Cell label={t("mac.fit_sequences")} value={String(fit.max_num_seqs)} />}
      </Box>
    </Paper>
  );
}
