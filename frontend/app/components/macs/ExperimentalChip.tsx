/**
 * Marks a Mac feature surface as experimental. Shown only where the console
 * already knows it is on a Mac (a standalone environment, a Mac machine, a
 * Mac fit); Linux and Ray pages never render it.
 */
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import { useTranslation } from "react-i18next";

export function ExperimentalChip() {
  const { t } = useTranslation();
  return (
    <Tooltip title={t("mac.experimental_help")} arrow>
      <Chip
        size="small"
        variant="outlined"
        color="warning"
        label={t("mac.experimental")}
        tabIndex={0}
        role="img"
        aria-label={`${t("mac.experimental")}. ${t("mac.experimental_help")}`}
        data-testid="experimental-chip"
      />
    </Tooltip>
  );
}
