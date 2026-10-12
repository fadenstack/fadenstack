import { useTranslation } from "react-i18next";

import type { PIIMode } from "~/api/mcp";
import { useServices } from "~/lib/ServicesContext";

import Alert from "@mui/material/Alert";
import type { SxProps, Theme } from "@mui/material/styles";

/**
 * Redact and Block need the PII service. Without it the MCP service refuses
 * every call to the server rather than send personal data unchecked, so say
 * so where the mode is chosen and shown.
 */
export function PiiServiceWarning({ mode, sx }: { mode: PIIMode; sx?: SxProps<Theme> }) {
  const { t } = useTranslation();
  const { isModuleEnabled, loading } = useServices();

  // While the module list loads it cannot be told; no warning flashes up.
  if (loading || mode === "allow" || isModuleEnabled("pii")) return null;
  return (
    <Alert severity="warning" sx={sx}>
      {t("mcp.pii_service_off_warning")}
    </Alert>
  );
}
