/**
 * ChatTopbar — the bar above the welcome view and the conversation: the way
 * into the navigation, a title, and the admin, language, theme and user
 * buttons. One bar for both views (they had a copy each).
 *
 * On a phone the logo stands at its start and opens the navigation panel;
 * elsewhere a menu button shows while the sidebar is closed.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";
import MenuIcon from "@mui/icons-material/Menu";
import TranslateIcon from "@mui/icons-material/Translate";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import type { UiLanguage } from "~/api/i18n";
import { NavLogoButton } from "~/components/NavLogoButton";
import { UserMenuButton } from "~/components/UserMenuButton";
import i18n from "~/i18n";
import { useThemeMode } from "~/theme-mode";
import { useChatShell } from "../ChatShellContext";

export interface ChatTopbarProps {
  title?: string;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
  languages: UiLanguage[];
  language: string;
  onLanguageChange: (code: string) => void;
  isSuperuser: boolean;
  /** A line under the bar (the conversation has one; the welcome view not). */
  divider?: boolean;
}

export default function ChatTopbar({
  title,
  onToggleSidebar,
  sidebarOpen,
  languages,
  language,
  onLanguageChange,
  isSuperuser,
  divider = false,
}: ChatTopbarProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useThemeMode();
  const shell = useChatShell();
  const [langMenuAnchor, setLangMenuAnchor] = useState<HTMLElement | null>(null);

  return (
    <Box
      component="header"
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        py: 1,
        minHeight: 56,
        ...(divider ? { borderBottom: 1, borderColor: "divider" } : {}),
      }}
    >
      {shell?.phoneNav ? (
        <NavLogoButton onClick={shell.phoneNav.onOpen} />
      ) : (
        !sidebarOpen && (
          <IconButton
            size="small"
            onClick={onToggleSidebar}
            aria-label={t("nav.open_navigation", { ns: "common" })}
          >
            <MenuIcon />
          </IconButton>
        )
      )}
      <Typography variant="body1" fontWeight={600} noWrap sx={{ flex: 1, minWidth: 0 }}>
        {title}
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {isSuperuser && (
          <Tooltip title={t("nav.admin", { defaultValue: "Admin panel" })}>
            <IconButton size="small" onClick={() => navigate("/admin")}>
              <AdminPanelSettingsIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        <Tooltip title={t("language.label", { defaultValue: "Language" })}>
          <IconButton size="small" onClick={(e) => setLangMenuAnchor(e.currentTarget)}>
            <TranslateIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Menu
          anchorEl={langMenuAnchor}
          open={Boolean(langMenuAnchor)}
          onClose={() => setLangMenuAnchor(null)}
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
                  .reloadResources([lang.code], ["common", "chat"])
                  .then(() => i18n.changeLanguage(lang.code));
                setLangMenuAnchor(null);
              }}
            >
              {lang.name}
            </MenuItem>
          ))}
        </Menu>
        <Tooltip
          title={
            mode === "dark"
              ? t("theme.light", { ns: "common", defaultValue: "Switch to light mode" })
              : t("theme.dark", { ns: "common", defaultValue: "Switch to dark mode" })
          }
        >
          <IconButton size="small" onClick={toggleMode}>
            {mode === "dark" ? <LightModeIcon fontSize="small" /> : <DarkModeIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        {shell && (
          <UserMenuButton
            email={shell.userEmail}
            onProfile={shell.onProfile}
            onLogout={shell.onLogout}
            iconSize="small"
          />
        )}
      </Box>
    </Box>
  );
}
