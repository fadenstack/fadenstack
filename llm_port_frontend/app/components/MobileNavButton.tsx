/**
 * The phone's way into the navigation: a floating button in the bottom
 * corner under the thumb of the hand the user picked. On a phone the sidebar
 * is not shown -- even collapsed it took a sixth of the width.
 */
import { useTranslation } from "react-i18next";
import Fab from "@mui/material/Fab";
import MenuIcon from "@mui/icons-material/Menu";
import { ADMIN_NAV_ID } from "~/components/AdminSidebar";
import type { NavHand } from "~/lib/useNavHand";

export interface MobileNavButtonProps {
  hand: NavHand;
  open: boolean;
  onOpen: () => void;
}

export function MobileNavButton({ hand, open, onOpen }: MobileNavButtonProps) {
  const { t } = useTranslation();
  const inset = hand === "left" ? "env(safe-area-inset-left, 0px)" : "env(safe-area-inset-right, 0px)";
  return (
    <Fab
      color="primary"
      onClick={onOpen}
      aria-label={t("nav.open_navigation")}
      aria-controls={ADMIN_NAV_ID}
      aria-expanded={open}
      aria-haspopup="dialog"
      data-hand={hand}
      sx={{
        position: "fixed",
        bottom: "calc(16px + env(safe-area-inset-bottom, 0px))",
        [hand]: `calc(16px + ${inset})`,
        zIndex: (theme) => theme.zIndex.speedDial,
      }}
    >
      <MenuIcon />
    </Fab>
  );
}
