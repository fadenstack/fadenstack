/**
 * The phone's way into the navigation: a floating button in the bottom
 * corner under the thumb of the hand the user picked. On a phone the sidebar
 * is not shown -- even collapsed it took a sixth of the width.
 */
import { useTranslation } from "react-i18next";
import Fab from "@mui/material/Fab";
import Zoom from "@mui/material/Zoom";
import type { SxProps, Theme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import type { NavHand } from "~/lib/useNavHand";

export interface MobileNavButtonProps {
  hand: NavHand;
  open: boolean;
  onOpen: () => void;
  /** The id of the panel it opens. */
  controls: string;
  /** False while it would be in the way (the chat's keyboard is up). */
  visible?: boolean;
  /** Placement overrides: the chat floats it above its composer. */
  sx?: SxProps<Theme>;
}

export function MobileNavButton({ hand, open, onOpen, controls, visible = true, sx }: MobileNavButtonProps) {
  const { t } = useTranslation();
  const inset = hand === "left" ? "env(safe-area-inset-left, 0px)" : "env(safe-area-inset-right, 0px)";
  return (
    <Zoom in={visible}>
      <Fab
        color="primary"
        onClick={onOpen}
        aria-label={t("nav.open_navigation", { ns: "common" })}
        aria-controls={controls}
        aria-expanded={open}
        aria-haspopup="dialog"
        data-hand={hand}
        sx={[
          {
            position: "fixed",
            bottom: "calc(16px + env(safe-area-inset-bottom, 0px))",
            [hand]: `calc(16px + ${inset})`,
            zIndex: (theme) => theme.zIndex.speedDial,
          },
          ...(Array.isArray(sx) ? sx : [sx]),
        ]}
      >
        <MenuIcon />
      </Fab>
    </Zoom>
  );
}
