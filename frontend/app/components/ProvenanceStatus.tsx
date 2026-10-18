/**
 * ProvenanceStatus — what the provenance engine says about itself, on the
 * module's card in Settings > Modules: the contract it speaks, its version,
 * what it can do and the plugins it loaded; or that it is not running.
 *
 * The backend asks the engine; an engine that does not answer comes back as
 * a state, never as an error.
 */
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { fetchProvenanceStatus, type ProvenanceStatus as Status } from "~/api/provenance";
import { useAsyncData } from "~/lib/useAsyncData";

interface Props {
  /** Changing it asks again (after a refresh or a switch). */
  refreshKey?: number;
}

function NotRunning({ status }: { status: Status }) {
  const { t } = useTranslation();
  const hint =
    status.state === "refused"
      ? t("provenance_status.refused_hint")
      : status.state === "unexpected"
        ? t("provenance_status.unexpected_hint")
        : t("provenance_status.not_running_hint");
  return (
    <Alert severity={status.enabled ? "warning" : "info"} data-testid="provenance-not-running">
      <Typography variant="body2" fontWeight={600}>
        {t(`provenance_status.state.${status.state}`)}
      </Typography>
      <Typography variant="body2">{hint}</Typography>
    </Alert>
  );
}

function Running({ status }: { status: Status }) {
  const { t } = useTranslation();
  const capabilities = Object.entries(status.capabilities);
  return (
    <Stack spacing={1} data-testid="provenance-running">
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip
          size="small"
          color="success"
          variant="outlined"
          label={t("provenance_status.state.running")}
        />
        <Typography variant="body2" data-testid="provenance-contract">
          {t("provenance_status.contract", { contract: status.contract })}
        </Typography>
        {status.engine_version && (
          <Typography variant="body2" color="text.secondary">
            {t("provenance_status.engine_version", { version: status.engine_version })}
          </Typography>
        )}
      </Stack>

      {!status.contract_supported && (
        <Alert severity="warning" data-testid="provenance-contract-unsupported">
          {t("provenance_status.contract_unsupported", {
            contract: status.contract,
            expected: status.expected_contract,
          })}
        </Alert>
      )}

      {capabilities.length > 0 && (
        <Box>
          <Typography variant="caption" color="text.secondary">
            {t("provenance_status.capabilities")}
          </Typography>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.5 }}>
            {capabilities.map(([name, available]) => {
              const label = t(`provenance_status.capability.${name}`, { defaultValue: name });
              return (
                <Chip
                  key={name}
                  size="small"
                  variant="outlined"
                  color={available ? "success" : "default"}
                  data-testid={`provenance-capability-${name}`}
                  data-available={available ? "true" : "false"}
                  label={
                    available
                      ? t("provenance_status.capability_available", { name: label })
                      : t("provenance_status.capability_unavailable", { name: label })
                  }
                />
              );
            })}
          </Stack>
        </Box>
      )}

      <Typography variant="body2" color="text.secondary" data-testid="provenance-plugins">
        {status.plugins.length > 0
          ? t("provenance_status.plugins", { plugins: status.plugins.join(", ") })
          : t("provenance_status.no_plugins")}
      </Typography>
    </Stack>
  );
}

export default function ProvenanceStatus({ refreshKey = 0 }: Props) {
  const { t } = useTranslation();
  const { data, loading, error } = useAsyncData<Status | null>(
    () => fetchProvenanceStatus(),
    [refreshKey],
    { initialValue: null },
  );

  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
        {t("provenance_status.title")}
      </Typography>
      {loading && !data ? (
        <CircularProgress size={18} aria-label={t("common.loading")} />
      ) : error && !data ? (
        <Alert severity="warning" data-testid="provenance-status-error">
          {t("provenance_status.load_failed")}
        </Alert>
      ) : data && data.state === "running" ? (
        <Running status={data} />
      ) : data ? (
        <NotRunning status={data} />
      ) : null}
    </Box>
  );
}
