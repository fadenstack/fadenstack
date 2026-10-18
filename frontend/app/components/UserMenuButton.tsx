/**
 * The signed-in user as a single round button: the email is in its tooltip
 * and at the top of its menu, not on the bar -- on a narrow screen it took a
 * third of it. Shared by the admin and chat top bars.
 */
import { useState, type ReactNode } from "react";
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
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";

import { AboutDialog } from "~/components/AboutDialog";
import { helpUrl } from "~/lib/pageHelp";

export interface UserMenuButtonProps {
  email: string;
  onProfile: () => void;
  onLogout: () => void;
  /** The icon size of the bar it sits in. */
  iconSize?: "small" | "medium";
  /** More entries after the profile (the chat's "My skills"). */
  extraItems?: { key: string; label: string; icon: ReactNode; onClick: () => void }[];
}

export function UserMenuButton({ email, onProfile, onLogout, iconSize = "medium", extraItems }: UserMenuButtonProps) {
  const { t, i18n } = useTranslation();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [aboutOpen, setAboutOpen] = useState(false);
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
        {extraItems?.map((item) => (
          <MenuItem
            key={item.key}
            onClick={() => {
              setAnchor(null);
              item.onClick();
            }}
          >
            <ListItemIcon>{item.icon}</ListItemIcon>
            <ListItemText>{item.label}</ListItemText>
          </MenuItem>
        ))}
        <Divider sx={{ my: 0.5 }} />
        {/* Help is this server's own; About names the release and links to the vendor's site. */}
        <MenuItem
          component="a"
          href={helpUrl(undefined, i18n.language)}
          target="_blank"
          rel="noopener"
          onClick={() => setAnchor(null)}
        >
          <ListItemIcon>
            <HelpOutlineIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("about.help")}</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setAnchor(null);
            setAboutOpen(true);
          }}
        >
          <ListItemIcon>
            <InfoOutlinedIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("about.menu_item")}</ListItemText>
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />
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
      <AboutDialog open={aboutOpen} onClose={() => setAboutOpen(false)} />
    </>
  );
}
