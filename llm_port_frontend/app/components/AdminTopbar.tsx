/**
 * AdminTopbar — top app bar for the admin layout.
 *
 * Contains help button, language selector, theme toggle,
 * root-mode controls, and user dropdown menu with profile/logout.
 */
import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { RootModeStatus } from "~/api/admin";
import type { UiLanguage } from "~/api/i18n";
import i18n from "~/i18n";

import AppBar from "@mui/material/AppBar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Toolbar from "@mui/material/Toolbar";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import ExploreIcon from "@mui/icons-material/Explore";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import LogoutIcon from "@mui/icons-material/Logout";
import ManageAccountsIcon from "@mui/icons-material/ManageAccounts";
import ReplayIcon from "@mui/icons-material/Replay";
import SecurityIcon from "@mui/icons-material/Security";
import TourIcon from "@mui/icons-material/Tour";
import TranslateIcon from "@mui/icons-material/Translate";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import SchoolIcon from "@mui/icons-material/School";

export interface AdminTopbarProps {
  mode: "light" | "dark";
  toggleMode: () => void;
  currentUserEmail: string;
  isSuperuser: boolean;
  rootStatus: RootModeStatus | null;
  languages: UiLanguage[];
  language: string;
  languageMenuAnchor: HTMLElement | null;
  onLanguageMenuOpen: (event: React.MouseEvent<HTMLElement>) => void;
  onLanguageMenuClose: () => void;
  onLanguageChange: (code: string) => void;
  onProductTourOpen: () => void;
  onGuidedSetupOpen: () => void;
  onHowToGuidesOpen: () => void;
  onResetGuides: () => void;
  onPageHelp: () => void;
  onRootFormOpen: () => void;
  onRootDeactivate: () => void;
  onLogout: () => void;
  /** Set on a phone, where there is no sidebar: the logo opens the navigation. */
  onOpenNav?: () => void;
}

export function AdminTopbar({
  mode,
  toggleMode,
  currentUserEmail,
  isSuperuser,
  rootStatus,
  languages,
  language,
  languageMenuAnchor,
  onLanguageMenuOpen,
  onLanguageMenuClose,
  onLanguageChange,
  onProductTourOpen,
  onGuidedSetupOpen,
  onHowToGuidesOpen,
  onResetGuides,
  onPageHelp,
  onRootFormOpen,
  onRootDeactivate,
  onLogout,
  onOpenNav,
}: AdminTopbarProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isRootActive = rootStatus?.active ?? false;

  // User dropdown menu state
  const [userMenuAnchor, setUserMenuAnchor] = useState<HTMLElement | null>(
    null,
  );
  // Help dropdown menu state
  const [helpMenuAnchor, setHelpMenuAnchor] = useState<HTMLElement | null>(
    null,
  );

  return (
    <AppBar position="static" elevation={0}>
      <Toolbar variant="dense" sx={{ justifyContent: "flex-end", gap: 1.5 }}>
        {onOpenNav && (
          <IconButton
            onClick={onOpenNav}
            aria-label={t("nav.open_navigation")}
            sx={{ mr: "auto", width: 36, height: 36, p: 0.5, borderRadius: 1.5 }}
          >
            <Box
              component="img"
              src="/icon_color.png"
              alt=""
              sx={{ width: "100%", height: "100%", objectFit: "contain", borderRadius: 1 }}
            />
          </IconButton>
        )}
        <Tooltip title={t("chat.title", { defaultValue: "Chat" })} arrow>
          <IconButton
            size="small"
            component="a"
            href="/chat"
            data-tour-id="topbar.chat"
            onClick={(e: React.MouseEvent<HTMLAnchorElement>) => {
              // Left-click without modifier → SPA navigation
              if (!e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
                e.preventDefault();
                navigate("/chat");
              }
              // Otherwise let the browser handle it (new tab / new window)
            }}
            sx={{ color: "text.primary" }}
          >
            <AutoAwesomeIcon />
          </IconButton>
        </Tooltip>
        <Tooltip title={t("help.title", { defaultValue: "Help" })} arrow>
          <IconButton
            size="small"
            data-tour-id="topbar.help"
            onClick={(e) => setHelpMenuAnchor(e.currentTarget)}
            sx={{ color: "text.primary" }}
          >
            <HelpOutlineIcon />
          </IconButton>
        </Tooltip>
        <Menu
          anchorEl={helpMenuAnchor}
          open={Boolean(helpMenuAnchor)}
          onClose={() => setHelpMenuAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 200 } } }}
        >
          <MenuItem
            onClick={() => {
              setHelpMenuAnchor(null);
              onPageHelp();
            }}
          >
            <ListItemIcon>
              <InfoOutlinedIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>
              {t("help.about_page", {
                ns: "tour",
                defaultValue: "About This Page",
              })}
            </ListItemText>
            <Chip
              label="F1"
              size="small"
              variant="outlined"
              sx={{ ml: 1, height: 20, fontSize: "0.7rem" }}
            />
          </MenuItem>
          <MenuItem
            onClick={() => {
              setHelpMenuAnchor(null);
              onProductTourOpen();
            }}
          >
            <ListItemIcon>
              <TourIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>
              {t("help.product_tour", {
                ns: "tour",
                defaultValue: "Product Tour",
              })}
            </ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => {
              setHelpMenuAnchor(null);
              onGuidedSetupOpen();
            }}
          >
            <ListItemIcon>
              <ExploreIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>
              {t("help.guided_setup", {
                ns: "tour",
                defaultValue: "Guided Setup",
              })}
            </ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => {
              setHelpMenuAnchor(null);
              onHowToGuidesOpen();
            }}
          >
            <ListItemIcon>
              <SchoolIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>
              {t("help.how_to_guides", {
                ns: "tour",
                defaultValue: "How-To Guides",
              })}
            </ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => {
              setHelpMenuAnchor(null);
              onResetGuides();
            }}
          >
            <ListItemIcon>
              <ReplayIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>
              {t("help.reset_guides", {
                ns: "tour",
                defaultValue: "Reset Guides",
              })}
            </ListItemText>
          </MenuItem>
        </Menu>
        <Tooltip title={t("language.label")} arrow>
          <IconButton
            size="small"
            data-tour-id="topbar.language"
            onClick={onLanguageMenuOpen}
            sx={{ color: "text.primary" }}
          >
            <TranslateIcon />
          </IconButton>
        </Tooltip>
        <Menu
          anchorEl={languageMenuAnchor}
          open={Boolean(languageMenuAnchor)}
          onClose={onLanguageMenuClose}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
        >
          {languages.map((lang) => (
            <MenuItem
              key={lang.code}
              selected={language === lang.code}
              onClick={() => {
                onLanguageChange(lang.code);
                void i18n
                  .reloadResources([lang.code], ["common"])
                  .then(() => i18n.changeLanguage(lang.code));
                onLanguageMenuClose();
              }}
            >
              {lang.name}
            </MenuItem>
          ))}
        </Menu>
        <Tooltip
          title={mode === "dark" ? t("theme.light") : t("theme.dark")}
          arrow
        >
          <IconButton
            size="small"
            data-tour-id="topbar.theme"
            onClick={toggleMode}
            sx={{ color: "text.primary" }}
          >
            {mode === "dark" ? (
              <LightModeOutlinedIcon />
            ) : (
              <DarkModeOutlinedIcon />
            )}
          </IconButton>
        </Tooltip>
        {isSuperuser && (
          <>
            {isRootActive ? (
              <>
                <Chip
                  icon={<SecurityIcon />}
                  label={t("root_mode.active")}
                  color="error"
                  size="small"
                  variant="filled"
                  // On a phone the red Deactivate button says it alone.
                  sx={{ fontWeight: 700, display: { xs: "none", sm: "inline-flex" } }}
                />
                <Button
                  size="small"
                  color="error"
                  variant="outlined"
                  onClick={onRootDeactivate}
                >
                  {t("root_mode.deactivate")}
                </Button>
              </>
            ) : (
              <Tooltip title={t("root_mode.activate")} arrow>
                <IconButton
                  size="small"
                  color="warning"
                  onClick={onRootFormOpen}
                  sx={{
                    width: 30,
                    height: 30,
                    border: (theme) =>
                      `1px solid ${theme.palette.warning.main}`,
                    borderRadius: "50%",
                  }}
                >
                  <SecurityIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </>
        )}
        {/* Just the avatar: the email took a third of the bar on a narrow
            screen. It is in the tooltip and at the top of the menu. */}
        <Tooltip title={t("profile.logged_in_as", { email: currentUserEmail })} arrow>
          <IconButton
            size="small"
            data-tour-id="topbar.profile"
            onClick={(e) => setUserMenuAnchor(e.currentTarget)}
            aria-label={t("profile.logged_in_as", { email: currentUserEmail })}
            aria-haspopup="menu"
            aria-expanded={Boolean(userMenuAnchor)}
            sx={{ color: "text.primary" }}
          >
            <AccountCircleIcon />
          </IconButton>
        </Tooltip>
        <Menu
          anchorEl={userMenuAnchor}
          open={Boolean(userMenuAnchor)}
          onClose={() => setUserMenuAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 220, maxWidth: 320 } } }}
        >
          {/* Who is signed in: a heading, not an action, so keyboard focus
              starts on the first item that does something. */}
          <Box sx={{ px: 2, pt: 1, pb: 1.25 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {t("profile.logged_in_label")}
            </Typography>
            <Typography variant="body2" sx={{ fontWeight: 600, wordBreak: "break-all" }}>
              {currentUserEmail}
            </Typography>
          </Box>
          <Divider sx={{ mb: 0.5 }} />
          <MenuItem
            onClick={() => {
              setUserMenuAnchor(null);
              navigate("/admin/profile");
            }}
          >
            <ListItemIcon>
              <ManageAccountsIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>{t("profile.manage_profile")}</ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => {
              setUserMenuAnchor(null);
              onLogout();
            }}
          >
            <ListItemIcon>
              <LogoutIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>{t("profile.logout")}</ListItemText>
          </MenuItem>
        </Menu>
      </Toolbar>
    </AppBar>
  );
}
