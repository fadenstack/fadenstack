/**
 * The signed-in user as a single round button: the email is in its tooltip
 * and at the top of its menu, not on the bar -- on a narrow screen it took a
 * third of it. Shared by the admin and chat top bars.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import LogoutIcon from "@mui/icons-material/Logout";
import ManageAccountsIcon from "@mui/icons-material/ManageAccounts";

export interface UserMenuButtonProps {
  email: string;
  onProfile: () => void;
  onLogout: () => void;
  /** The icon size of the bar it sits in. */
  iconSize?: "small" | "medium";
}

export function UserMenuButton({ email, onProfile, onLogout, iconSize = "medium" }: UserMenuButtonProps) {
  const { t } = useTranslation();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const label = t("profile.logged_in_as", { ns: "common", email });

  return (
    <>
      <Tooltip title={label} arrow>
        <IconButton
          size="small"
          data-tour-id="topbar.profile"
          onClick={(e) => setAnchor(e.currentTarget)}
          aria-label={label}
          aria-haspopup="menu"
          aria-expanded={Boolean(anchor)}
          sx={{ color: "text.primary" }}
        >
          <AccountCircleIcon fontSize={iconSize} />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 220, maxWidth: 320 } } }}
      >
        {/* Who is signed in: a heading, not an action, so keyboard focus
            starts on the first item that does something. */}
        <Box sx={{ px: 2, pt: 1, pb: 1.25 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            {t("profile.logged_in_label", { ns: "common" })}
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600, wordBreak: "break-all" }}>
            {email}
          </Typography>
        </Box>
        <Divider sx={{ mb: 0.5 }} />
        <MenuItem
          onClick={() => {
            setAnchor(null);
            onProfile();
          }}
        >
          <ListItemIcon>
            <ManageAccountsIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("profile.manage_profile", { ns: "common" })}</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setAnchor(null);
            onLogout();
          }}
        >
          <ListItemIcon>
            <LogoutIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("profile.logout", { ns: "common" })}</ListItemText>
        </MenuItem>
      </Menu>
    </>
  );
}
