/**
 * ChatSidebar — session list grouped by project with project CRUD.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListSubheader from "@mui/material/ListSubheader";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import Tooltip from "@mui/material/Tooltip";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import TextField from "@mui/material/TextField";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Collapse from "@mui/material/Collapse";
import { useTheme } from "@mui/material/styles";
import AddIcon from "@mui/icons-material/Add";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import FolderIcon from "@mui/icons-material/Folder";
import CreateNewFolderIcon from "@mui/icons-material/CreateNewFolder";
import MenuIcon from "@mui/icons-material/Menu";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import DriveFileMoveOutlinedIcon from "@mui/icons-material/DriveFileMoveOutlined";
import { useTranslation } from "react-i18next";

import type { ChatSession, ChatProject } from "~/api/chatTypes";
import ProjectSettingsDialog from "./ProjectSettingsDialog";

interface Props {
  open: boolean;
  onToggle: () => void;
  sessions: ChatSession[];
  projects: ChatProject[];
  activeSessionId?: string;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onCreateProject: (name: string) => Promise<ChatProject>;
  onDeleteProject: (id: string) => void;
  onUpdateProject: (
    id: string,
    updates: { name?: string; description?: string; system_instructions?: string },
  ) => Promise<void>;
  onMoveSession: (sessionId: string, projectId: string | null) => void;
  width: number;
  isMobile: boolean;
}

export default function ChatSidebar({
  open,
  onToggle,
  sessions,
  projects,
  activeSessionId,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onCreateProject,
  onDeleteProject,
  onUpdateProject,
  onMoveSession,
  width,
  isMobile,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();

  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(
    new Set(),
  );
  // Move-to-project menu state
  const [moveAnchor, setMoveAnchor] = useState<{
    el: HTMLElement;
    sessionId: string;
  } | null>(null);
  // Project settings dialog state
  const [editingProject, setEditingProject] = useState<ChatProject | null>(
    null,
  );

  // Partition sessions by project
  const orphanSessions = sessions.filter((s) => !s.project_id);
  const sessionsByProject = new Map<string, ChatSession[]>();
  for (const s of sessions) {
    if (s.project_id) {
      const list = sessionsByProject.get(s.project_id) ?? [];
      list.push(s);
      sessionsByProject.set(s.project_id, list);
    }
  }

  const toggleProject = (id: string) => {
    setCollapsedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCreateProject = async () => {
    const name = newProjectName.trim();
    if (!name) return;
    await onCreateProject(name);
    setNewProjectName("");
    setCreatingProject(false);
  };

  const drawerContent = (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        bgcolor: theme.palette.background.default,
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.5,
          py: 1,
          minHeight: 56,
        }}
      >
        <Typography variant="h6" noWrap sx={{ fontSize: "1rem" }}>
          {t("chat.title", { defaultValue: "Chat" })}
        </Typography>
        <Box>
          <Tooltip
            title={t("chat.new_project", { defaultValue: "New project" })}
          >
            <IconButton size="small" onClick={() => setCreatingProject(true)}>
              <CreateNewFolderIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("chat.new_chat", { defaultValue: "New chat" })}>
            <IconButton size="small" onClick={onNewChat}>
              <AddIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          {isMobile && (
            <IconButton size="small" onClick={onToggle}>
              <MenuIcon fontSize="small" />
            </IconButton>
          )}
        </Box>
      </Box>
      <Divider />

      {/* New-project inline form */}
      {creatingProject && (
        <Box sx={{ px: 1.5, py: 1 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder={t("chat.project_name", {
              defaultValue: "Project name",
            })}
            value={newProjectName}
            onChange={(e) => setNewProjectName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateProject();
              if (e.key === "Escape") setCreatingProject(false);
            }}
          />
          <Box
            sx={{
              display: "flex",
              gap: 0.5,
              mt: 0.5,
              justifyContent: "flex-end",
            }}
          >
            <Button size="small" onClick={() => setCreatingProject(false)}>
              {t("common.cancel", { defaultValue: "Cancel" })}
            </Button>
            <Button
              size="small"
              variant="contained"
              onClick={handleCreateProject}
              disabled={!newProjectName.trim()}
            >
              {t("common.create", { defaultValue: "Create" })}
            </Button>
          </Box>
        </Box>
      )}

      {/* Session list */}
      <Box sx={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>
        {/* Projects */}
        {projects.map((proj) => {
          const collapsed = collapsedProjects.has(proj.id);
          const projSessions = sessionsByProject.get(proj.id) ?? [];

          return (
            <Box key={proj.id}>
              <ListSubheader
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  bgcolor: "transparent",
                  cursor: "pointer",
                  lineHeight: "32px",
                  px: 1.5,
                }}
                onClick={() => toggleProject(proj.id)}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                  <FolderIcon sx={{ fontSize: 16 }} />
                  <Typography variant="caption" fontWeight={600} noWrap>
                    {proj.name}
                  </Typography>
                </Box>
                <Box>
                  {collapsed ? (
                    <ExpandMoreIcon sx={{ fontSize: 16 }} />
                  ) : (
                    <ExpandLessIcon sx={{ fontSize: 16 }} />
                  )}
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditingProject(proj);
                    }}
                    sx={{ ml: 0.5 }}
                  >
                    <SettingsOutlinedIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteProject(proj.id);
                    }}
                    sx={{ ml: 0.5 }}
                  >
                    <DeleteOutlineIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                </Box>
              </ListSubheader>
              <Collapse in={!collapsed}>
                <List dense disablePadding>
                  {projSessions.map((s) => (
                    <SessionItem
                      key={s.id}
                      session={s}
                      active={s.id === activeSessionId}
                      onSelect={onSelectSession}
                      onDelete={onDeleteSession}
                      onMoveClick={(el) =>
                        setMoveAnchor({ el, sessionId: s.id })
                      }
                    />
                  ))}
                </List>
              </Collapse>
            </Box>
          );
        })}

        {/* Unorganised sessions */}
        {orphanSessions.length > 0 && (
          <>
            {projects.length > 0 && <Divider sx={{ my: 0.5 }} />}
            <List dense disablePadding>
              {orphanSessions.map((s) => (
                <SessionItem
                  key={s.id}
                  session={s}
                  active={s.id === activeSessionId}
                  onSelect={onSelectSession}
                  onDelete={onDeleteSession}
                  onMoveClick={(el) => setMoveAnchor({ el, sessionId: s.id })}
                />
              ))}
            </List>
          </>
        )}
      </Box>

      {/* Move-to-project popover menu */}
      <Menu
        anchorEl={moveAnchor?.el}
        open={!!moveAnchor}
        onClose={() => setMoveAnchor(null)}
      >
        <MenuItem
          dense
          onClick={() => {
            if (moveAnchor) onMoveSession(moveAnchor.sessionId, null);
            setMoveAnchor(null);
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {t("chat.no_project", { defaultValue: "No project" })}
          </Typography>
        </MenuItem>
        {projects.map((p) => (
          <MenuItem
            key={p.id}
            dense
            onClick={() => {
              if (moveAnchor) onMoveSession(moveAnchor.sessionId, p.id);
              setMoveAnchor(null);
            }}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <FolderIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary={p.name} />
          </MenuItem>
        ))}
      </Menu>

      {/* Project settings dialog */}
      <ProjectSettingsDialog
        open={!!editingProject}
        project={editingProject}
        onClose={() => setEditingProject(null)}
        onSave={onUpdateProject}
      />
    </Box>
  );

  // Mobile: temporary drawer. Desktop: persistent.
  if (isMobile) {
    return (
      <Drawer
        variant="temporary"
        open={open}
        onClose={onToggle}
        sx={{ "& .MuiDrawer-paper": { width } }}
      >
        {drawerContent}
      </Drawer>
    );
  }

  return (
    <Drawer
      variant="persistent"
      open={open}
      sx={{
        "& .MuiDrawer-paper": {
          width,
          borderRight: `1px solid ${theme.palette.divider}`,
          boxSizing: "border-box",
        },
      }}
    >
      {drawerContent}
    </Drawer>
  );
}

// ── SessionItem (internal) ───────────────────────────────────────

function SessionItem({
  session,
  active,
  onSelect,
  onDelete,
  onMoveClick,
}: {
  session: ChatSession;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onMoveClick: (el: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);

  return (
    <ListItemButton
      selected={active}
      onClick={() => onSelect(session.id)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      sx={{ pl: session.project_id ? 3 : 1.5, pr: 1 }}
    >
      <ListItemIcon sx={{ minWidth: 28 }}>
        <ChatBubbleOutlineIcon sx={{ fontSize: 16 }} />
      </ListItemIcon>
      <ListItemText
        primary={
          session.title || t("chat.untitled", { defaultValue: "Untitled chat" })
        }
        primaryTypographyProps={{
          noWrap: true,
          fontSize: "0.85rem",
        }}
      />
      {hovered && (
        <Box sx={{ display: "flex", ml: 0.5 }}>
          <Tooltip
            title={t("chat.move_to_project", {
              defaultValue: "Move to project",
            })}
          >
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                onMoveClick(e.currentTarget);
              }}
            >
              <DriveFileMoveOutlinedIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("common.delete", { defaultValue: "Delete" })}>
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(session.id);
              }}
            >
              <DeleteOutlineIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        </Box>
      )}
    </ListItemButton>
  );
}
