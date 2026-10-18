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

import MenuBookOutlinedIcon from "@mui/icons-material/MenuBookOutlined";
import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import ChecklistIcon from "@mui/icons-material/Checklist";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import ReplayIcon from "@mui/icons-material/Replay";
import SecurityIcon from "@mui/icons-material/Security";
import TourIcon from "@mui/icons-material/Tour";
import TranslateIcon from "@mui/icons-material/Translate";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import SchoolIcon from "@mui/icons-material/School";

import AgentAvatar from "~/components/AgentAvatar";
import { NavLogoButton } from "~/components/NavLogoButton";
import { UserMenuButton } from "~/components/UserMenuButton";
import { AboutDialog } from "~/components/AboutDialog";
import { helpUrl } from "~/lib/pageHelp";
import { pickLanguage } from "~/lib/userLanguage";

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
  onGettingStartedOpen: () => void;
  onConsoleTourOpen: () => void;
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
  onGettingStartedOpen,
  onConsoleTourOpen,
  onHowToGuidesOpen,
  onResetGuides,
  onPageHelp,
  onRootFormOpen,
  onRootDeactivate,
  onLogout,
  onOpenNav,
}: AdminTopbarProps) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const isRootActive = rootStatus?.active ?? false;

  // Help dropdown menu state
  const [aboutOpen, setAboutOpen] = useState(false);
  const [helpMenuAnchor, setHelpMenuAnchor] = useState<HTMLElement | null>(
    null,
  );
  const helpItems = [
    {
      key: "getting_started",
      icon: ChecklistIcon,
      onClick: onGettingStartedOpen,
    },
    { key: "console_tour", icon: TourIcon, onClick: onConsoleTourOpen },
    {
      key: "how_to_guides",
      icon: SchoolIcon,
      onClick: onHowToGuidesOpen,
      divider: true,
    },
    { key: "reset_guides", icon: ReplayIcon, onClick: onResetGuides },
  ];

  return (
    <AppBar position="static" elevation={0}>
      <Toolbar variant="dense" sx={{ justifyContent: "flex-end", gap: 1.5 }}>
        {onOpenNav && (
          <Box sx={{ mr: "auto" }}>
            <NavLogoButton onClick={onOpenNav} />
          </Box>
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
            <AgentAvatar size={24} variant="mono" />
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
          slotProps={{ paper: { sx: { minWidth: 220 } } }}
        >
          <MenuItem
            data-help-item="about_page"
            onClick={() => {
              setHelpMenuAnchor(null);
              onPageHelp();
            }}
          >
            <ListItemIcon>
              <InfoOutlinedIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>{t("help.about_page", { ns: "tour" })}</ListItemText>
            <Chip
              label="F1"
              size="small"
              variant="outlined"
              sx={{ ml: 1, height: 20, fontSize: "0.7rem" }}
            />
          </MenuItem>
          <MenuItem
            data-help-item="help_pages"
            component="a"
            href={helpUrl(undefined, i18n.language)}
            target="_blank"
            rel="noopener"
            onClick={() => setHelpMenuAnchor(null)}
          >
            <ListItemIcon>
              <MenuBookOutlinedIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>{t("help.help_pages", { ns: "tour" })}</ListItemText>
          </MenuItem>
          {helpItems.map(({ key, icon: Icon, onClick, divider }) => (
            <MenuItem
              key={key}
              divider={divider}
              data-help-item={key}
              onClick={() => {
                setHelpMenuAnchor(null);
                onClick();
              }}
            >
              <ListItemIcon>
                <Icon fontSize="small" />
              </ListItemIcon>
              <ListItemText>{t(`help.${key}`, { ns: "tour" })}</ListItemText>
            </MenuItem>
          ))}
          <Divider sx={{ my: 0.5 }} />
          <MenuItem
            data-help-item="about_fadenstack"
            onClick={() => {
              setHelpMenuAnchor(null);
              setAboutOpen(true);
            }}
          >
            <ListItemIcon>
              <InfoOutlinedIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>{t("about.menu_item")}</ListItemText>
          </MenuItem>
        </Menu>
        <AboutDialog open={aboutOpen} onClose={() => setAboutOpen(false)} />
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
                pickLanguage(lang.code);
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
                  sx={{
                    fontWeight: 700,
                    display: { xs: "none", sm: "inline-flex" },
                  }}
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
        <UserMenuButton
          email={currentUserEmail}
          onProfile={() => navigate("/admin/profile")}
          onLogout={onLogout}
        />
      </Toolbar>
    </AppBar>
  );
}
