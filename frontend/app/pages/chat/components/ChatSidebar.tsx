/**
 * ChatSidebar — the chat's navigation, in the admin console's look: the
 * brand at the top, a search, then two sections -- Projects (each a
 * folder of its chats) and Chats (the rest, grouped by when they were last
 * used). Chats move between projects by drag and drop or a menu.
 *
 * On a desktop it collapses to a rail (logo, new chat, search) like the admin
 * sidebar; below 900 px it is an overlay, and on a phone it slides in from
 * the side of the user's hand. On a touch screen there is no hover to reveal
 * a chat's actions and a drag would take over scrolling, so each chat has a
 * ⋮ menu instead and dragging is off.
 *
 * Search, for now, matches titles only, in the list already loaded: instant,
 * no request, and titles are encrypted at rest so the database could not
 * match them anyway. Searching what was said in chats needs its own index to
 * stay fast -- see the note at `matches`.
 */
import { useEffect, useRef, useState, useCallback, type ReactNode } from "react";
import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import ListItemIcon from "@mui/material/ListItemIcon";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Tooltip from "@mui/material/Tooltip";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import TextField from "@mui/material/TextField";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Collapse from "@mui/material/Collapse";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import AddIcon from "@mui/icons-material/Add";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import CloseIcon from "@mui/icons-material/Close";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import FolderIcon from "@mui/icons-material/Folder";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import CreateNewFolderIcon from "@mui/icons-material/CreateNewFolder";
import MoreVertIcon from "@mui/icons-material/MoreVert";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import SearchIcon from "@mui/icons-material/Search";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import DriveFileMoveOutlinedIcon from "@mui/icons-material/DriveFileMoveOutlined";
import { useTranslation } from "react-i18next";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDroppable,
  useDraggable,
  closestCenter,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";

import type { ChatSession, ChatProject } from "~/api/chatTypes";
import AppBrand from "~/components/AppBrand";
import { NavHandPicker, phonePanelPaperSx } from "~/components/NavHandPicker";
import { DRAWER_WIDTH_CLOSED, linkButtonSx, navActiveSx } from "~/lib/adminConstants";
import BrandMark from "~/components/BrandMark";
import type { NavHand } from "~/lib/useNavHand";
import { CHAT_NAV_ID } from "../ChatShellContext";
import ProjectSettingsDialog from "./ProjectSettingsDialog";

/** Chats rendered at first, and added per step as the list scrolls. With every
 *  chat rendered at once, 3,400 of them held the page for 17 s. */
const SESSION_STEP = 50;
/** Chats rendered at first inside a project, and added per "Show more". */
const PROJECT_STEP = 20;
/** The collapsed desktop sidebar: the admin console's rail width. */
export const CHAT_RAIL_WIDTH = DRAWER_WIDTH_CLOSED;

const NO_PROJECT = "__no_project__";

type DateGroup = "today" | "yesterday" | "week" | "month" | "older";

/** When a chat was last used, as the Chats section groups it (local days). */
export function dateGroupOf(iso: string, now: Date = new Date()): DateGroup {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(now) - day(new Date(iso))) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return "week";
  if (days < 30) return "month";
  return "older";
}

interface Props {
  /** Expanded (desktop) or shown (overlay). */
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
    updates: {
      name?: string;
      description?: string;
      system_instructions?: string;
    },
  ) => Promise<void>;
  onMoveSession: (sessionId: string, projectId: string | null) => void;
  width: number;
  /** Below 900 px: an overlay instead of a rail. */
  isMobile: boolean;
  /** Set on a phone: the panel comes in from this side, with the side picker. */
  phone?: { hand: NavHand; onHandChange: (hand: NavHand) => void };
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
  phone,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  // No hover (a touch screen): actions behind a ⋮ button, no dragging.
  const touch = !useMediaQuery("(hover: hover)");
  const touchButton = touch ? { width: 40, height: 40 } : {};
  const untitled = t("chat.untitled", { defaultValue: "Untitled chat" });

  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const focusSearchOnOpen = useRef(false);
  // A chat's actions on a touch screen
  const [moreAnchor, setMoreAnchor] = useState<{ el: HTMLElement; sessionId: string } | null>(null);
  // Move-to-project menu state
  const [moveAnchor, setMoveAnchor] = useState<{ el: HTMLElement; sessionId: string } | null>(null);
  // Project settings dialog state
  const [editingProject, setEditingProject] = useState<ChatProject | null>(null);
  // Drag-and-drop state
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const activeDragSession = activeDragId ? sessions.find((s) => s.id === activeDragId) : null;

  // The rail's search button opens the sidebar with the search focused.
  useEffect(() => {
    if (open && focusSearchOnOpen.current) {
      focusSearchOnOpen.current = false;
      setTimeout(() => searchRef.current?.focus(), 0);
    }
  }, [open]);

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveDragId(String(event.active.id));
  }, []);

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDragId(null);
      const { active, over } = event;
      if (!over) return;
      const sessionId = String(active.id);
      const targetId = String(over.id);
      const projectId = targetId === NO_PROJECT ? null : targetId;
      const session = sessions.find((s) => s.id === sessionId);
      if (!session || (session.project_id ?? null) === projectId) return;
      onMoveSession(sessionId, projectId);
    },
    [sessions, onMoveSession],
  );

  // How much of each list is rendered: the chats, the search results, and
  // per project.
  const [orphanShown, setOrphanShown] = useState(SESSION_STEP);
  const [resultsShown, setResultsShown] = useState(SESSION_STEP);
  const [projectShown, setProjectShown] = useState<Record<string, number>>({});
  // At least up to the open chat, so it is in the list.
  const shownOf = (list: ChatSession[], base: number) =>
    Math.max(base, list.findIndex((s) => s.id === activeSessionId) + 1);

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
  const projectName = new Map(projects.map((p) => [p.id, p.name]));

  // Search: titles only, over the list already loaded. Searching what was
  // said needs a server-side index to stay fast -- the messages are
  // encrypted at rest, so a database LIKE cannot read them, and decrypting a
  // user's history per keystroke would not scale. The plan: a per-user
  // keyword index of keyed hashes (a blind index), written when a message is
  // stored, queried by the gateway; the content itself stays encrypted.
  const q = query.trim().toLocaleLowerCase();
  const matches = q ? sessions.filter((s) => (s.title || untitled).toLocaleLowerCase().includes(q)) : [];

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

  const item = (s: ChatSession, secondary?: string) => (
    <DraggableSessionItem
      key={s.id}
      session={s}
      secondary={secondary}
      active={s.id === activeSessionId}
      onSelect={onSelectSession}
      onDelete={onDeleteSession}
      onMoveClick={(el) => setMoveAnchor({ el, sessionId: s.id })}
      // No dragging while searching: the folders are not shown to drop on.
      touch={touch || Boolean(q)}
      onMoreClick={(el) => setMoreAnchor({ el, sessionId: s.id })}
    />
  );

  // ── The collapsed desktop rail ───────────────────────────────────
  if (!isMobile && !open) {
    return (
      <Drawer
        variant="permanent"
        sx={{
          "& .MuiDrawer-paper": {
            width: CHAT_RAIL_WIDTH,
            boxSizing: "border-box",
            overflowX: "hidden",
            borderRight: `1px solid ${theme.palette.divider}`,
          },
        }}
      >
        <Stack alignItems="center" gap={1} sx={{ pt: 2 }}>
          <Tooltip title={t("nav.expand", { defaultValue: "Expand" })} placement="right" arrow>
            <LogoButton onClick={onToggle} label={t("nav.expand", { defaultValue: "Expand" })} />
          </Tooltip>
          <Tooltip title={t("chat.new_chat", { defaultValue: "New chat" })} placement="right" arrow>
            <IconButton onClick={onNewChat} aria-label={t("chat.new_chat", { defaultValue: "New chat" })}>
              <AddIcon />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("chat.search_placeholder")} placement="right" arrow>
            <IconButton
              onClick={() => {
                focusSearchOnOpen.current = true;
                onToggle();
              }}
              aria-label={t("chat.search_placeholder")}
            >
              <SearchIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      </Drawer>
    );
  }

  const drawerContent = (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Brand, as the admin console has it; its logo collapses the sidebar */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, px: 2, py: 2 }}>
        <LogoButton
          onClick={onToggle}
          label={isMobile ? t("nav.close_navigation") : t("nav.collapse", { defaultValue: "Collapse" })}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <AppBrand subtitle={t("chat.title", { defaultValue: "Chat" })} />
        </Box>
        {isMobile && (
          <IconButton onClick={onToggle} aria-label={t("nav.close_navigation")} sx={{ width: 44, height: 44 }}>
            <CloseIcon />
          </IconButton>
        )}
      </Box>

      {/* New chat, and the search */}
      <Stack gap={1} sx={{ px: 1.5, pb: 1 }}>
        <Button
          variant="outlined"
          startIcon={<AddIcon />}
          onClick={onNewChat}
          sx={{ justifyContent: "flex-start", textTransform: "none", minHeight: touch ? 44 : 36 }}
        >
          {t("chat.new_chat", { defaultValue: "New chat" })}
        </Button>
        <TextField
          inputRef={searchRef}
          size="small"
          fullWidth
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setResultsShown(SESSION_STEP);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setQuery("");
          }}
          placeholder={t("chat.search_placeholder")}
          slotProps={{
            htmlInput: { "aria-label": t("chat.search_placeholder") },
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" sx={{ color: "text.disabled" }} />
                </InputAdornment>
              ),
              endAdornment: query ? (
                <InputAdornment position="end">
                  <IconButton size="small" edge="end" onClick={() => setQuery("")} aria-label={t("chat.search_clear")}>
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ) : undefined,
            },
          }}
        />
      </Stack>

      {/* New-project inline form */}
      {creatingProject && (
        <Box sx={{ px: 1.5, pb: 1 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder={t("chat.project_name", { defaultValue: "Project name" })}
            value={newProjectName}
            onChange={(e) => setNewProjectName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateProject();
              if (e.key === "Escape") setCreatingProject(false);
            }}
          />
          <Box sx={{ display: "flex", gap: 0.5, mt: 0.5, justifyContent: "flex-end" }}>
            <Button size="small" onClick={() => setCreatingProject(false)}>
              {t("common.cancel", { defaultValue: "Cancel" })}
            </Button>
            <Button size="small" variant="contained" onClick={handleCreateProject} disabled={!newProjectName.trim()}>
              {t("common.create", { defaultValue: "Create" })}
            </Button>
          </Box>
        </Box>
      )}

      {/* The list */}
      <Box sx={{ flex: 1, overflowY: "auto", overflowX: "hidden", pb: 1 }}>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
          {q ? (
            <>
              <SectionHeader label={t("chat.search_results", { matches: matches.length })} />
              {matches.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ px: 2.5, py: 1 }}>
                  {t("chat.no_results", { defaultValue: "No chats match" })}
                </Typography>
              ) : (
                <List dense disablePadding>
                  {matches
                    .slice(0, resultsShown)
                    .map((s) => item(s, s.project_id ? projectName.get(s.project_id) : undefined))}
                  {matches.length > resultsShown && (
                    <ShowMore auto remaining={matches.length - resultsShown} onMore={() => setResultsShown((n) => n + SESSION_STEP)} />
                  )}
                </List>
              )}
            </>
          ) : (
            <>
              {/* Projects */}
              <SectionHeader
                label={t("chat.section_projects", { defaultValue: "Projects" })}
                action={
                  <Tooltip title={t("chat.new_project", { defaultValue: "New project" })}>
                    <IconButton
                      size="small"
                      onClick={() => setCreatingProject(true)}
                      aria-label={t("chat.new_project", { defaultValue: "New project" })}
                      sx={touchButton}
                    >
                      <CreateNewFolderIcon sx={{ fontSize: 18 }} />
                    </IconButton>
                  </Tooltip>
                }
              />
              {projects.length === 0 && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2.5, pb: 1 }}>
                  {t("chat.no_projects", { defaultValue: "Group related chats in a project." })}
                </Typography>
              )}
              {projects.map((proj) => {
                const collapsed = collapsedProjects.has(proj.id);
                const projSessions = sessionsByProject.get(proj.id) ?? [];
                const projShown = shownOf(projSessions, projectShown[proj.id] ?? PROJECT_STEP);
                return (
                  <DroppableProjectZone key={proj.id} projectId={proj.id}>
                    <ListItemButton
                      onClick={() => toggleProject(proj.id)}
                      aria-expanded={!collapsed}
                      sx={{ ...linkButtonSx, mx: 1, mb: 0.25, pr: 0.5, ...(touch ? { minHeight: 48 } : {}) }}
                    >
                      <ListItemIcon sx={{ minWidth: 32, color: "text.secondary" }}>
                        {collapsed ? <FolderIcon fontSize="small" /> : <FolderOpenIcon fontSize="small" />}
                      </ListItemIcon>
                      <ListItemText
                        primary={proj.name}
                        secondary={projSessions.length || undefined}
                        slotProps={{
                          primary: { noWrap: true, fontSize: "0.875rem", fontWeight: 600 },
                          secondary: { component: "span", sx: { ml: 1, fontSize: "0.75rem" } },
                        }}
                        sx={{ display: "flex", alignItems: "baseline", minWidth: 0, my: 0 }}
                      />
                      <Tooltip title={t("chat.project_settings", { defaultValue: "Project settings" })}>
                        <IconButton
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingProject(proj);
                          }}
                          sx={touchButton}
                        >
                          <SettingsOutlinedIcon sx={{ fontSize: 16 }} />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={t("common.delete", { defaultValue: "Delete" })}>
                        <IconButton
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteProject(proj.id);
                          }}
                          sx={touchButton}
                        >
                          <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                        </IconButton>
                      </Tooltip>
                      <ExpandMoreIcon
                        sx={{ fontSize: 18, color: "text.secondary", transform: collapsed ? "none" : "rotate(180deg)", transition: "transform 150ms" }}
                      />
                    </ListItemButton>
                    <Collapse in={!collapsed}>
                      <List dense disablePadding sx={{ pl: 2 }}>
                        {projSessions.slice(0, projShown).map((s) => item(s))}
                        {projSessions.length > projShown && (
                          <ShowMore
                            remaining={projSessions.length - projShown}
                            onMore={() => setProjectShown((prev) => ({ ...prev, [proj.id]: projShown + PROJECT_STEP }))}
                          />
                        )}
                      </List>
                    </Collapse>
                  </DroppableProjectZone>
                );
              })}

              {/* Chats, by when they were last used */}
              <DroppableProjectZone projectId={NO_PROJECT}>
                <SectionHeader label={t("chat.section_chats", { defaultValue: "Chats" })} />
                {orphanSessions.length === 0 ? (
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2.5, pb: 1 }}>
                    {projects.length > 0
                      ? t("chat.drop_here_no_project", { defaultValue: "Drop here to remove from project" })
                      : t("chat.no_chats", { defaultValue: "No chats yet." })}
                  </Typography>
                ) : (
                  <List dense disablePadding>
                    {groupedByDate(orphanSessions.slice(0, shownOf(orphanSessions, orphanShown))).map(([group, list]) => (
                      <Box key={group}>
                        <Typography
                          variant="caption"
                          color="text.disabled"
                          sx={{ display: "block", px: 2.5, pt: 1, pb: 0.25, fontWeight: 600 }}
                        >
                          {t(`chat.group_${group}`)}
                        </Typography>
                        {list.map((s) => item(s))}
                      </Box>
                    ))}
                    {orphanSessions.length > shownOf(orphanSessions, orphanShown) && (
                      <ShowMore
                        auto
                        remaining={orphanSessions.length - shownOf(orphanSessions, orphanShown)}
                        onMore={() => setOrphanShown(shownOf(orphanSessions, orphanShown) + SESSION_STEP)}
                      />
                    )}
                  </List>
                )}
              </DroppableProjectZone>
            </>
          )}

          {/* Drag overlay — floating ghost */}
          <DragOverlay dropAnimation={null}>
            {activeDragSession ? (
              <Box
                sx={{
                  bgcolor: "action.selected",
                  borderRadius: 1,
                  px: 1.5,
                  py: 0.5,
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  opacity: 0.9,
                  boxShadow: 3,
                }}
              >
                <ChatBubbleOutlineIcon sx={{ fontSize: 16 }} />
                <Typography variant="body2" noWrap sx={{ fontSize: "0.85rem" }}>
                  {activeDragSession.title || untitled}
                </Typography>
              </Box>
            ) : null}
          </DragOverlay>
        </DndContext>
      </Box>

      {/* Collapse (desktop), as the admin sidebar's foot has it */}
      {!isMobile && (
        <Box sx={{ display: "flex", justifyContent: "flex-end", px: 1, pb: 0.5 }}>
          <Tooltip title={t("nav.collapse", { defaultValue: "Collapse" })}>
            <IconButton size="small" onClick={onToggle} aria-label={t("nav.collapse", { defaultValue: "Collapse" })}>
              <ChevronLeftIcon />
            </IconButton>
          </Tooltip>
        </Box>
      )}

      {/* Which side the menu button is on (phone) */}
      {phone && <NavHandPicker hand={phone.hand} onChange={phone.onHandChange} />}

      {/* A chat's actions on a touch screen */}
      <Menu anchorEl={moreAnchor?.el} open={!!moreAnchor} onClose={() => setMoreAnchor(null)}>
        <MenuItem
          sx={{ minHeight: 44 }}
          onClick={() => {
            if (moreAnchor) setMoveAnchor(moreAnchor);
            setMoreAnchor(null);
          }}
        >
          <ListItemIcon>
            <DriveFileMoveOutlinedIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("chat.move_to_project", { defaultValue: "Move to project" })}</ListItemText>
        </MenuItem>
        <MenuItem
          sx={{ minHeight: 44 }}
          onClick={() => {
            if (moreAnchor) onDeleteSession(moreAnchor.sessionId);
            setMoreAnchor(null);
          }}
        >
          <ListItemIcon>
            <DeleteOutlineIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("common.delete", { defaultValue: "Delete" })}</ListItemText>
        </MenuItem>
      </Menu>

      {/* Move-to-project popover menu */}
      <Menu anchorEl={moveAnchor?.el} open={!!moveAnchor} onClose={() => setMoveAnchor(null)}>
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

  // Overlay below 900 px (from the hand's side on a phone); a rail-or-panel
  // on a desktop.
  if (isMobile) {
    return (
      <Drawer
        variant="temporary"
        anchor={phone?.hand ?? "left"}
        open={open}
        onClose={onToggle}
        ModalProps={{ keepMounted: true }}
        slotProps={{
          paper: {
            id: CHAT_NAV_ID,
            role: "dialog",
            "aria-label": t("nav.navigation"),
            sx: phone ? phonePanelPaperSx(phone.hand) : { width },
          },
        }}
      >
        {drawerContent}
      </Drawer>
    );
  }

  return (
    <Drawer
      variant="permanent"
      sx={{
        "& .MuiDrawer-paper": {
          width,
          borderRight: `1px solid ${theme.palette.divider}`,
          boxSizing: "border-box",
          overflowX: "hidden",
        },
      }}
    >
      {drawerContent}
    </Drawer>
  );
}

/** Chats in the order given, cut into runs by when they were last used. */
function groupedByDate(list: ChatSession[]): [DateGroup, ChatSession[]][] {
  const now = new Date();
  const runs: [DateGroup, ChatSession[]][] = [];
  for (const s of list) {
    const group = dateGroupOf(s.updated_at, now);
    const last = runs[runs.length - 1];
    if (last && last[0] === group) last[1].push(s);
    else runs.push([group, [s]]);
  }
  return runs;
}

// ── Parts (internal) ─────────────────────────────────────────────

/** The logo as a button, as the admin sidebar's. */
function LogoButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <IconButton
      onClick={onClick}
      aria-label={label}
      sx={{ width: 40, height: 40, borderRadius: 1, flexShrink: 0, p: 0, "&:hover": { bgcolor: "action.hover" } }}
    >
      <BrandMark size={36} />
    </IconButton>
  );
}

/** A section's name in small caps, as the admin sidebar's "Pinned". */
function SectionHeader({ label, action }: { label: string; action?: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 2.5, pt: 1.5, pb: 0.5, minHeight: 36 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ textTransform: "uppercase", letterSpacing: 1, fontWeight: 700, fontSize: "0.68rem" }}
      >
        {label}
      </Typography>
      {action}
    </Stack>
  );
}

/**
 * Renders the next part of a long list. With ``auto`` it does so on its own
 * when scrolled into view; it is a button either way (keyboard, and where
 * IntersectionObserver is missing).
 */
function ShowMore({ remaining, onMore, auto = false }: { remaining: number; onMore: () => void; auto?: boolean }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const onMoreRef = useRef(onMore);
  onMoreRef.current = onMore;

  useEffect(() => {
    const el = ref.current;
    if (!auto || !el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) onMoreRef.current();
      },
      // A screen ahead, so the list has grown before the user gets there.
      { rootMargin: "0px 0px 400px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [auto]);

  return (
    <ListItemButton ref={ref} onClick={onMore} sx={{ ...linkButtonSx, mx: 1, minHeight: 40 }}>
      <ListItemText
        primary={t("chat.show_more", { remaining, defaultValue: "Show more ({{remaining}})" })}
        slotProps={{ primary: { fontSize: "0.8rem", color: "text.secondary" } }}
      />
    </ListItemButton>
  );
}

function DroppableProjectZone({ projectId, children }: { projectId: string; children: ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: projectId });
  const theme = useTheme();
  return (
    <Box
      ref={setNodeRef}
      sx={{
        transition: "background-color 150ms ease",
        bgcolor: isOver ? theme.palette.action.hover : "transparent",
        borderRadius: 1,
        ...(isOver && { outline: `2px dashed ${theme.palette.primary.main}`, outlineOffset: -2 }),
      }}
    >
      {children}
    </Box>
  );
}

interface SessionItemProps {
  session: ChatSession;
  /** A line under the title (the project, in search results). */
  secondary?: string;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onMoveClick: (el: HTMLElement) => void;
  touch: boolean;
  onMoreClick: (el: HTMLElement) => void;
}

function DraggableSessionItem(props: SessionItemProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: props.session.id,
    disabled: props.touch,
  });
  // Without dragging (a touch screen) its attributes would make the row a
  // disabled button (aria-disabled): announced as disabled, though it opens.
  return (
    <Box ref={setNodeRef} {...listeners} {...(props.touch ? {} : attributes)} sx={{ opacity: isDragging ? 0.4 : 1 }}>
      <SessionItem {...props} />
    </Box>
  );
}

/** A chat in the list; the open one is highlighted as the admin's current page. */
function SessionItem({ session, secondary, active, onSelect, onDelete, onMoveClick, touch, onMoreClick }: SessionItemProps) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);

  return (
    <ListItemButton
      onClick={() => onSelect(session.id)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      aria-current={active ? "page" : undefined}
      sx={{
        ...linkButtonSx,
        mx: 1,
        mb: 0.25,
        pl: 1.5,
        pr: 0.5,
        ...(touch ? { minHeight: 48 } : {}),
        ...(active ? { ...navActiveSx, "&:hover": { bgcolor: "accent.navActiveBg" } } : {}),
      }}
    >
      <ListItemIcon sx={{ minWidth: 28, color: "text.secondary" }}>
        <ChatBubbleOutlineIcon sx={{ fontSize: 16 }} />
      </ListItemIcon>
      <ListItemText
        primary={session.title || t("chat.untitled", { defaultValue: "Untitled chat" })}
        secondary={secondary}
        slotProps={{
          primary: { noWrap: true, fontSize: "0.85rem", fontWeight: active ? 600 : 400 },
          secondary: { noWrap: true, fontSize: "0.72rem" },
        }}
        sx={{ my: 0.25 }}
      />
      {touch && (
        <IconButton
          edge="end"
          aria-label={t("chat.session_actions", { defaultValue: "Chat actions" })}
          onClick={(e) => {
            e.stopPropagation();
            onMoreClick(e.currentTarget);
          }}
          sx={{ width: 40, height: 40, color: "inherit" }}
        >
          <MoreVertIcon fontSize="small" />
        </IconButton>
      )}
      {!touch && hovered && (
        <Box sx={{ display: "flex", ml: 0.5 }}>
          <Tooltip title={t("chat.move_to_project", { defaultValue: "Move to project" })}>
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                onMoveClick(e.currentTarget);
              }}
              sx={{ color: "inherit" }}
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
              sx={{ color: "inherit" }}
            >
              <DeleteOutlineIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        </Box>
      )}
    </ListItemButton>
  );
}
