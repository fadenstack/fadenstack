/**
 * Left or right: which bottom corner the phone's menu button sits in (and
 * which side its panel slides in from). Shown at the foot of the panel, where
 * the thumb already is.
 */
import { useId } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import FrontHandOutlinedIcon from "@mui/icons-material/FrontHandOutlined";
import type { NavHand } from "~/lib/useNavHand";

export function NavHandPicker({ hand, onChange }: { hand: NavHand; onChange: (hand: NavHand) => void }) {
  const { t } = useTranslation();
  const labelId = useId();
  return (
    <Box sx={{ px: 2, pt: 1, pb: 1.5, borderTop: 1, borderColor: "divider" }}>
      <Typography id={labelId} variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.75 }}>
        {t("nav.button_side", { ns: "common" })}
      </Typography>
      <ToggleButtonGroup
        exclusive
        fullWidth
        size="small"
        value={hand}
        onChange={(_e, value: NavHand | null) => value && onChange(value)}
        aria-labelledby={labelId}
      >
        <ToggleButton value="left" sx={{ minHeight: 44, gap: 1, textTransform: "none" }}>
          <FrontHandOutlinedIcon fontSize="small" sx={{ transform: "scaleX(-1)" }} />
          {t("nav.side_left", { ns: "common" })}
        </ToggleButton>
        <ToggleButton value="right" sx={{ minHeight: 44, gap: 1, textTransform: "none" }}>
          <FrontHandOutlinedIcon fontSize="small" />
          {t("nav.side_right", { ns: "common" })}
        </ToggleButton>
      </ToggleButtonGroup>
    </Box>
  );
}

/** The phone panel's paper: rounded on the edge that faces the page. */
export function phonePanelPaperSx(hand: NavHand) {
  return {
    width: "min(300px, 85vw)",
    boxSizing: "border-box" as const,
    overflowX: "hidden" as const,
    pb: "env(safe-area-inset-bottom, 0px)",
    ...(hand === "right"
      ? { borderTopLeftRadius: 12, borderBottomLeftRadius: 12 }
      : { borderTopRightRadius: 12, borderBottomRightRadius: 12 }),
  };
}
