/**
 * ChatTopbar — the bar above the welcome view and the conversation: the way
 * into the navigation, a title, and the admin, language, theme and user
 * buttons. One bar for both views (they had a copy each).
 *
 * On a phone the logo stands at its start and opens the navigation panel;
 * elsewhere a menu button shows while the sidebar is closed.
 */
import { useState, type FormEvent } from "react";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import DataUsageIcon from "@mui/icons-material/DataUsage";
import InsightsIcon from "@mui/icons-material/Insights";
import PsychologyIcon from "@mui/icons-material/Psychology";
import AgentAvatar from "~/components/AgentAvatar";
import InputBase from "@mui/material/InputBase";
import Stack from "@mui/material/Stack";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";
import MenuIcon from "@mui/icons-material/Menu";
import TranslateIcon from "@mui/icons-material/Translate";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import type { UiLanguage } from "~/api/i18n";
import { NavLogoButton } from "~/components/NavLogoButton";
import { UserMenuButton } from "~/components/UserMenuButton";
import { pickLanguage } from "~/lib/userLanguage";
import { useThemeMode } from "~/theme-mode";
import { useChatShell } from "../ChatShellContext";

export interface ChatTopbarProps {
  title?: string;
  /** Set when the title can be renamed (an open chat): the pencil saves through it. */
  onRename?: (title: string) => Promise<void>;
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
  onRename,
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
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {onRename && title ? (
          <RenameableTitle title={title} onRename={onRename} />
        ) : (
          <Typography variant="body1" fontWeight={600} noWrap>
            {title}
          </Typography>
        )}
      </Box>
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
                pickLanguage(lang.code, ["common", "chat"]);
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
            extraItems={[
              shell.onSkills && {
                key: "skills",
                label: t("skills.my_title"),
                icon: <PsychologyIcon fontSize="small" />,
                onClick: shell.onSkills,
              },
              shell.onAgents && {
                key: "agents",
                label: t("agents.title", { ns: "common" }),
                icon: <AgentAvatar size={20} variant="mono" />,
                onClick: shell.onAgents,
              },
              shell.onCheckContent && {
                key: "check-content",
                label: t("nav.check_content", { ns: "common" }),
                icon: <FactCheckIcon fontSize="small" />,
                onClick: shell.onCheckContent,
              },
              shell.onUsage && {
                key: "usage",
                label: t("usage.my.menu", { ns: "common" }),
                icon: <DataUsageIcon fontSize="small" />,
                onClick: shell.onUsage,
              },
              shell.onTeamUsage && {
                key: "team-usage",
                label: t("usage.admin.menu", { ns: "common" }),
                icon: <InsightsIcon fontSize="small" />,
                onClick: shell.onTeamUsage,
              },
            ].filter((item) => !!item)}
          />
        )}
      </Box>
    </Box>
  );
}

/**
 * The chat's title, and a pencil to rename it in place: Enter or the tick
 * saves, Escape or the cross cancels. An empty or unchanged name saves
 * nothing.
 */
function RenameableTitle({ title, onRename }: { title: string; onRename: (title: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [saving, setSaving] = useState(false);

  async function save(e?: FormEvent) {
    e?.preventDefault();
    const next = draft.trim();
    if (!next || next === title) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onRename(next);
      setEditing(false);
    } catch {
      // Kept open with the text as typed; the user can try again or cancel.
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
        <Typography variant="body1" fontWeight={600} noWrap title={title}>
          {title}
        </Typography>
        <Tooltip title={t("chat.rename", { defaultValue: "Rename" })}>
          <IconButton
            size="small"
            onClick={() => {
              setDraft(title);
              setEditing(true);
            }}
            aria-label={t("chat.rename", { defaultValue: "Rename" })}
            sx={{ flexShrink: 0, color: "text.secondary", "@media (pointer: coarse)": { width: 40, height: 40 } }}
          >
            <EditOutlinedIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>
    );
  }

  return (
    <Stack component="form" direction="row" alignItems="center" gap={0.5} onSubmit={save} sx={{ minWidth: 0 }}>
      <InputBase
        autoFocus
        value={draft}
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setEditing(false);
        }}
        onFocus={(e) => e.target.select()}
        inputProps={{ maxLength: 200, "aria-label": t("chat.title_label", { defaultValue: "Chat title" }) }}
        sx={{
          flex: 1,
          minWidth: 0,
          fontWeight: 600,
          px: 1,
          borderRadius: 1,
          border: 1,
          borderColor: "primary.main",
          // 16 px so a phone does not zoom in on focus.
          fontSize: { xs: "1rem", sm: "0.95rem" },
        }}
      />
      <Tooltip title={t("chat.rename_save", { defaultValue: "Save" })}>
        <span>
          <IconButton type="submit" size="small" disabled={saving} aria-label={t("chat.rename_save", { defaultValue: "Save" })}>
            <CheckIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title={t("chat.rename_cancel", { defaultValue: "Cancel" })}>
        <IconButton size="small" onClick={() => setEditing(false)} aria-label={t("chat.rename_cancel", { defaultValue: "Cancel" })}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
