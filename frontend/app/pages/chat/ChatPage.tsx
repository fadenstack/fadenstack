/**
 * ChatPage — main standalone chat page with sidebar, welcome, and chat window.
 *
 * Route: /chat (welcome) and /chat/:sessionId (active session).
 *
 * Below 900 px the sidebar is an overlay; on a phone (below 600 px) it is
 * opened from a floating button above the composer, on the side of the hand
 * the user picked -- the same button and choice as the admin console.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useOutletContext, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import IconButton from "@mui/material/IconButton";
import CloseIcon from "@mui/icons-material/Close";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import Drawer from "@mui/material/Drawer";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import { chatApi, type ChatSession, type ChatProject } from "~/api/chatClient";
import { auth, type AuthUser } from "~/api/auth";
import { listLanguages, type UiLanguage } from "~/api/i18n";
import { clearCachedAccess } from "~/lib/adminConstants";
import { patchSessionPiiPolicy } from "~/api/pii";
import { patchSessionToolPolicy } from "~/api/tools";
import { agentRights } from "~/api/agents";
import { skillRights } from "~/api/skills";
import CreateSkillDialog from "~/components/skills/CreateSkillDialog";
import { useCan } from "~/lib/useCan";
import { useContentCheck } from "~/lib/useContentCheck";
import { useSkillsModule } from "~/lib/useSkillsModule";
import type { InitialMessageState } from "./components/ChatWelcome";
import ChatSidebar, { CHAT_RAIL_WIDTH } from "./components/ChatSidebar";
import ChatWelcome from "./components/ChatWelcome";
import ChatWindow from "./components/ChatWindow";
import ExecutionModeSelector from "./components/ExecutionModeSelector";
import ToolPanel from "./components/ToolPanel";
import PiiPanel from "./components/PiiPanel";
import { useToolPolicy } from "./hooks/useToolPolicy";
import { useSessionPiiPolicy } from "./hooks/useSessionPiiPolicy";
import { ChatShellContext, type ChatShell } from "./ChatShellContext";
import { useNavHand } from "~/lib/useNavHand";
import { ChatSkillsProvider, useChatSkillsState } from "./hooks/useChatSkills";
import { mergeSessions, withSession, withWriting } from "./sessionList";
import ProfilePage from "../admin/ProfilePage";

const SIDEBAR_WIDTH = 260;
/** How often the chats list is asked while a reply is written in one of them. */
const WRITING_POLL_MS = 5_000;

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId?: string }>();
  const { user } = useOutletContext<{ user: AuthUser }>();
  const can = useCan();
  const canDebug = can("chat.debug:read");
  const skillsAllowed = skillRights((p) => can(p));
  const agentsAllowed = agentRights(can).open;
  const skillsModule = useSkillsModule();
  const checkAllowed = useContentCheck(can("provenance:check")).state === "on";
  const navigate = useNavigate();
  const { t, i18n } = useTranslation("chat");
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const [navHand, setNavHand] = useNavHand();

  // State
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(!isMobile);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [languages, setLanguages] = useState<UiLanguage[]>([]);
  const [language, setLanguage] = useState<string>(i18n.resolvedLanguage || i18n.language || "en");
  const [search] = useSearchParams();
  // A provider linked from My Profile comes back here with how it went: My Profile says it.
  const [profileOpen, setProfileOpen] = useState(() => search.has("linked") || search.has("link_refused"));
  const [toolDrawerOpen, setToolDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState(0);
  const { executionMode, setExecutionMode } = useToolPolicy(sessionId ?? null);
  const {
    policy: piiPolicy,
    loading: piiLoading,
    error: piiError,
    localOverrides: localPiiOverrides,
    refresh: piiRefresh,
    updateOverride: piiUpdate,
    clearOverride: piiClear,
  } = useSessionPiiPolicy(sessionId ?? null);
  const [localToolOverrides, setLocalToolOverrides] = useState<
    Map<string, boolean>
  >(new Map());
  const chatSkills = useChatSkillsState({
    sessionId,
    enabled: skillsModule.enabled && skillsAllowed.read,
    canCreate: skillsAllowed.create,
  });
  const { adopt: adoptSkills } = chatSkills;

  const handleLocalToolOverride = useCallback(
    (toolId: string, enabled: boolean) => {
      setLocalToolOverrides((prev) => new Map(prev).set(toolId, enabled));
    },
    [],
  );

  // Refs
  const loadedRef = useRef(false);

  // Load sidebar data
  const refreshSidebar = useCallback(async () => {
    try {
      const [sess, proj] = await Promise.all([
        chatApi.listSessions(),
        chatApi.listProjects(),
      ]);
      // Merged, not replaced: a chat started while this loaded stays.
      setSessions((prev) => mergeSessions(sess, prev));
      setProjects(proj);
    } catch {
      // Gateway may not be available yet
    }
  }, []);

  useEffect(() => {
    if (!loadedRef.current) {
      loadedRef.current = true;
      refreshSidebar();
      listLanguages()
        .then(setLanguages)
        .catch(() => {});
    }
  }, [refreshSidebar]);

  // Close mobile sidebar on navigation
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [sessionId, isMobile]);

  // Handlers
  // Picking the chat already open changes no route, so the effect above
  // would leave the overlay open over it.
  const handleNewChat = useCallback(() => {
    navigate("/chat");
    if (isMobile) setSidebarOpen(false);
  }, [navigate, isMobile]);

  const handleSelectSession = useCallback(
    (id: string) => {
      navigate(`/chat/${id}`);
      if (isMobile) setSidebarOpen(false);
    },
    [navigate, isMobile],
  );

  const handleSessionCreated = useCallback(
    async (sess: ChatSession, initialState?: InitialMessageState) => {
      setSessions((prev) => [sess, ...prev]);
      // Apply pre-session execution mode and tool overrides to the new session
      const hasOverrides = localToolOverrides.size > 0;
      if (executionMode !== "server_only" || hasOverrides) {
        try {
          const patch: Record<string, unknown> = {};
          if (executionMode !== "server_only") {
            patch.execution_mode = executionMode;
          }
          if (hasOverrides) {
            patch.tool_overrides = Array.from(localToolOverrides.entries()).map(
              ([tool_id, enabled]) => ({ tool_id, enabled }),
            );
          }
          await patchSessionToolPolicy(sess.id, patch);
        } catch {
          // Best-effort — session still works with defaults
        }
      }
      // Flush pre-session PII overrides
      const hasPiiOverrides = Object.values(localPiiOverrides).some(
        (v) => v != null,
      );
      if (hasPiiOverrides) {
        try {
          await patchSessionPiiPolicy(sess.id, localPiiOverrides);
        } catch {
          // Best-effort — session still works with defaults
        }
      }
      // Clear local overrides now that they're persisted
      setLocalToolOverrides(new Map());
      // The skills picked before the first message stay with the chat.
      adoptSkills(sess.id);
      navigate(`/chat/${sess.id}`, { state: initialState });
    },
    [navigate, executionMode, localToolOverrides, localPiiOverrides, adoptSkills],
  );

  const handleDeleteSession = useCallback(
    async (id: string) => {
      await chatApi.deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (sessionId === id) navigate("/chat");
    },
    [sessionId, navigate],
  );

  const handleCreateProject = useCallback(async (name: string) => {
    const proj = await chatApi.createProject({ name });
    setProjects((prev) => [...prev, proj]);
    return proj;
  }, []);

  const handleDeleteProject = useCallback(
    async (id: string) => {
      await chatApi.deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
      await refreshSidebar();
    },
    [refreshSidebar],
  );

  const handleMoveSession = useCallback(
    async (sessId: string, projectId: string | null) => {
      await chatApi.updateSession(sessId, {
        // null takes it out of its project (undefined would change nothing).
        project_id: projectId,
      });
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessId ? { ...s, project_id: projectId } : s,
        ),
      );
    },
    [],
  );

  const handleUpdateProject = useCallback(
    async (
      id: string,
      updates: {
        name?: string;
        description?: string;
        system_instructions?: string;
      },
    ) => {
      await chatApi.updateProject(id, updates);
      setProjects((prev) =>
        prev.map((p) => (p.id === id ? { ...p, ...updates } : p)),
      );
    },
    [],
  );

  const handleRenameSession = useCallback(
    async (title: string) => {
      if (!sessionId) return;
      const updated = await chatApi.updateSession(sessionId, { title });
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? { ...s, title: updated.title ?? title } : s)),
      );
    },
    [sessionId],
  );

  const handleLogout = useCallback(async () => {
    await auth.logout();
    clearCachedAccess();
    navigate("/");
  }, [navigate]);

  const handleLanguageChange = useCallback((code: string) => {
    setLanguage(code);
  }, []);

  // The menu shows the language the console is in, also when it follows the user's saved one.
  useEffect(() => {
    const follow = (code: string) => setLanguage(code);
    i18n.on("languageChanged", follow);
    return () => i18n.off("languageChanged", follow);
  }, [i18n]);

  const handleSessionUpdated = useCallback((updated: ChatSession) => {
    setSessions((prev) => withSession(prev, updated));
  }, []);

  const handleWritingChange = useCallback((id: string, writing: boolean) => {
    setSessions((prev) => withWriting(prev, id, writing));
  }, []);

  // While a reply is written in any chat, the list is asked every few
  // seconds: a reply goes on after the user left its chat, and its mark
  // goes once it is done.
  const anyWriting = sessions.some((s) => s.writing);
  useEffect(() => {
    if (!anyWriting) return;
    const timer = window.setInterval(() => void refreshSidebar(), WRITING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [anyWriting, refreshSidebar]);

  // Collapsed on a desktop, the sidebar is the admin console's rail.
  const sidebarWidth = sidebarOpen ? SIDEBAR_WIDTH : CHAT_RAIL_WIDTH;
  // The bar's menu button is for the overlay; on a desktop the rail's logo
  // opens the sidebar.
  const barSidebarOpen = isMobile ? sidebarOpen : true;
  const activeSession = sessionId ? sessions.find((s) => s.id === sessionId) : undefined;

  const shell = useMemo<ChatShell>(
    () => ({
      userEmail: user.email,
      onProfile: () => setProfileOpen(true),
      onLogout: handleLogout,
      onSkills: chatSkills.value.enabled ? () => navigate("/skills") : undefined,
      onAgents: agentsAllowed ? () => navigate("/agents") : undefined,
      onCheckContent: checkAllowed ? () => navigate("/check-content") : undefined,
      phoneNav: isPhone
        ? { hand: navHand, open: sidebarOpen, onOpen: () => setSidebarOpen(true) }
        : null,
    }),
    [
      user.email,
      handleLogout,
      isPhone,
      navHand,
      sidebarOpen,
      chatSkills.value.enabled,
      agentsAllowed,
      checkAllowed,
      navigate,
    ],
  );

  // A phone's 100vh reaches behind the browser's toolbar.
  const fullHeight = { height: "100vh", "@supports (height: 100dvh)": { height: "100dvh" } };

  return (
    <ChatSkillsProvider value={chatSkills.value}>
    <ChatShellContext.Provider value={shell}>
    <Box sx={{ display: "flex", ...fullHeight, overflow: "hidden" }}>
      {/* Sidebar */}
      <ChatSidebar
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((v) => !v)}
        sessions={sessions}
        projects={projects}
        activeSessionId={sessionId}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        onCreateProject={handleCreateProject}
        onDeleteProject={handleDeleteProject}
        onUpdateProject={handleUpdateProject}
        onMoveSession={handleMoveSession}
        width={SIDEBAR_WIDTH}
        isMobile={isMobile}
        phone={isPhone ? { hand: navHand, onHandChange: setNavHand } : undefined}
      />

      {/* Main content */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          ...fullHeight,
          minWidth: 0,
          ml: isMobile ? 0 : `${sidebarWidth}px`,
          transition: "margin-left 0.2s ease",
        }}
      >
        {sessionId ? (
          <ChatWindow
            key={sessionId}
            sessionId={sessionId}
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
            onSessionCreated={handleSessionCreated}
            onSessionUpdated={handleSessionUpdated}
            onWritingChange={handleWritingChange}
            onToggleSidebar={() => setSidebarOpen((v) => !v)}
            sidebarOpen={barSidebarOpen}
            languages={languages}
            language={language}
            onLanguageChange={handleLanguageChange}
            isSuperuser={user.is_superuser}
            canDebug={canDebug}
            onToolsToggle={() => setToolDrawerOpen((o) => !o)}
            toolsOpen={toolDrawerOpen}
            title={activeSession?.title}
            onRename={handleRenameSession}
          />
        ) : (
          <ChatWelcome
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
            onSessionCreated={handleSessionCreated}
            onToggleSidebar={() => setSidebarOpen((v) => !v)}
            sidebarOpen={barSidebarOpen}
            languages={languages}
            language={language}
            onLanguageChange={handleLanguageChange}
            isSuperuser={user.is_superuser}
            onToolsToggle={() => setToolDrawerOpen((o) => !o)}
            toolsOpen={toolDrawerOpen}
          />
        )}

        {/* Tool routing drawer — shared across welcome + active session */}
        <Drawer
          anchor="right"
          open={toolDrawerOpen}
          onClose={() => setToolDrawerOpen(false)}
          variant="temporary"
          ModalProps={{ keepMounted: true }}
          PaperProps={{
            sx: {
              width: "min(320px, 100vw)",
              mt: "56px",
              height: "calc(100% - 56px)",
              display: "flex",
              flexDirection: "column",
            },
          }}
          slotProps={{ backdrop: { sx: { backgroundColor: "transparent" } } }}
        >
          <Tabs
            value={drawerTab}
            onChange={(_, v) => setDrawerTab(v)}
            variant="fullWidth"
            sx={{ borderBottom: 1, borderColor: "divider", flexShrink: 0 }}
          >
            <Tab label={t("tools_tab", "Tools")} />
            <Tab label={t("privacy_tab", "Privacy")} />
          </Tabs>

          {drawerTab === 0 && (
            <Box sx={{ flex: 1, overflow: "auto" }}>
              <Box sx={{ p: 1.5 }}>
                <ExecutionModeSelector
                  sessionId={sessionId ?? null}
                  value={executionMode}
                  onChange={setExecutionMode}
                />
              </Box>
              <ToolPanel
                active={toolDrawerOpen}
                sessionId={sessionId ?? null}
                executionMode={executionMode}
                localOverrides={localToolOverrides}
                onLocalOverride={handleLocalToolOverride}
              />
            </Box>
          )}

          {drawerTab === 1 && (
            <Box sx={{ flex: 1, overflow: "auto" }}>
              <PiiPanel
                policy={piiPolicy}
                loading={piiLoading}
                error={piiError}
                onUpdate={piiUpdate}
                onClear={piiClear}
                onRefresh={piiRefresh}
              />
            </Box>
          )}
        </Drawer>
      </Box>

      {/* Profile overlay */}
      <Dialog
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        maxWidth="sm"
        fullWidth
        fullScreen={isPhone}
      >
        <DialogTitle
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          {""}
          <IconButton
            size="small"
            onClick={() => setProfileOpen(false)}
            aria-label={t("common.close", { ns: "common", defaultValue: "Close" })}
          >
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          <ProfilePage />
        </DialogContent>
      </Dialog>

      {/* Create a skill: /skill, or the skills menu in the input */}
      <CreateSkillDialog
        open={chatSkills.createFor !== null}
        sessionId={sessionId}
        model={selectedModel}
        initialGuidance={chatSkills.createFor?.guidance}
        inChat
        onClose={chatSkills.closeCreate}
        onCreated={(skill, useNow) => chatSkills.created(skill, useNow)}
      />
    </Box>
    </ChatShellContext.Provider>
    </ChatSkillsProvider>
  );
}
