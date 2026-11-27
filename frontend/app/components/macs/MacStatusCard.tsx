/**
 * What an operator should know about a Mac before it serves models: whether
 * the native runtime is there, the sandbox, the application firewall (it can
 * block the gateway) and FileVault (an unattended reboot needs authrestart).
 * Read from the agent's reported capabilities; anything it did not report
 * says so instead of guessing.
 */
import Alert from "@mui/material/Alert";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

export interface MacStatus {
  filevault?: boolean | null;
  firewall?: { enabled?: boolean | null; stealth?: boolean | null; block_all?: boolean | null } | null;
}

export function MacStatusCard({ capabilities }: { capabilities: Record<string, unknown> | null | undefined }) {
  const { t } = useTranslation();
  const caps = capabilities ?? {};
  const status = (caps.mac_status ?? {}) as MacStatus;
  // The agent reports the working set only once a runtime is staged.
  const staged = Boolean((caps.metal as Record<string, unknown> | undefined)?.recommended_max_working_set_bytes);
  const sandbox = caps.native_sandbox;
  const firewall = status.firewall?.enabled ?? null;
  const vault = status.filevault ?? null;

  return (
    <Paper variant="outlined" sx={{ p: 2 }} data-testid="mac-status">
      <Stack spacing={1}>
        <Typography variant="subtitle2">{t("mac.status_title")}</Typography>
        <Alert severity={staged ? "success" : "info"} data-testid="mac-runtime">
          {staged ? t("mac.runtime_ready") : t("mac.runtime_missing")}
        </Alert>
        <Alert severity={sandbox === true ? "success" : sandbox === false ? "warning" : "info"} data-testid="mac-sandbox">
          {sandbox === true ? t("mac.sandbox_on") : sandbox === false ? t("mac.sandbox_off") : t("mac.sandbox_unknown")}
        </Alert>
        <Alert severity={firewall === true ? "warning" : "info"} data-testid="mac-firewall">
          {firewall === true ? t("mac.firewall_on") : firewall === false ? t("mac.firewall_off") : t("mac.firewall_unknown")}
        </Alert>
        <Alert severity="info" data-testid="mac-filevault">
          {vault === true ? t("mac.filevault_on") : vault === false ? t("mac.filevault_off") : t("mac.filevault_unknown")}
        </Alert>
      </Stack>
    </Paper>
  );
}
