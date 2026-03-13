/**
 * ChatPage — main standalone chat page with sidebar, welcome, and chat window.
 *
 * Route: /chat (welcome) and /chat/:sessionId (active session).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useOutletContext, useLocation } from "react-router";
import Box from "@mui/material/Box";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import { chatApi, type ChatSession, type ChatProject } from "~/api/chatClient";
import type { AuthUser } from "~/api/auth";
import type { InitialMessageState } from "./components/ChatWelcome";
import ChatSidebar from "./components/ChatSidebar";
import ChatWelcome from "./components/ChatWelcome";
import ChatWindow from "./components/ChatWindow";

const SIDEBAR_WIDTH = 260;
const SIDEBAR_COLLAPSED_WIDTH = 0;

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId?: string }>();
  const { user } = useOutletContext<{ user: AuthUser }>();
  const navigate = useNavigate();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));

  // State
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(!isMobile);
  const [selectedModel, setSelectedModel] = useState<string>("");

  // Refs
  const loadedRef = useRef(false);

  // Load sidebar data
  const refreshSidebar = useCallback(async () => {
    try {
      const [sess, proj] = await Promise.all([
        chatApi.listSessions(),
        chatApi.listProjects(),
      ]);
      setSessions(sess);
      setProjects(proj);
    } catch {
      // Gateway may not be available yet
    }
  }, []);

  useEffect(() => {
    if (!loadedRef.current) {
      loadedRef.current = true;
      refreshSidebar();
    }
  }, [refreshSidebar]);

  // Close mobile sidebar on navigation
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [sessionId, isMobile]);

  // Handlers
  const handleNewChat = useCallback(() => {
    navigate("/chat");
  }, [navigate]);

  const handleSelectSession = useCallback(
    (id: string) => navigate(`/chat/${id}`),
    [navigate],
  );

  const handleSessionCreated = useCallback(
    (sess: ChatSession, initialState?: InitialMessageState) => {
      setSessions((prev) => [sess, ...prev]);
      navigate(`/chat/${sess.id}`, { state: initialState });
    },
    [navigate],
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
        project_id: projectId ?? undefined,
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
      updates: { name?: string; description?: string; system_instructions?: string },
    ) => {
      await chatApi.updateProject(id, updates);
      setProjects((prev) =>
        prev.map((p) => (p.id === id ? { ...p, ...updates } : p)),
      );
    },
    [],
  );

  const handleSessionUpdated = useCallback((updated: ChatSession) => {
    setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }, []);

  const sidebarWidth = sidebarOpen ? SIDEBAR_WIDTH : SIDEBAR_COLLAPSED_WIDTH;

  return (
    <Box sx={{ display: "flex", height: "100vh", overflow: "hidden" }}>
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
      />

      {/* Main content */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          height: "100vh",
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
            onToggleSidebar={() => setSidebarOpen((v) => !v)}
            sidebarOpen={sidebarOpen}
          />
        ) : (
          <ChatWelcome
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
            onSessionCreated={handleSessionCreated}
            onToggleSidebar={() => setSidebarOpen((v) => !v)}
            sidebarOpen={sidebarOpen}
          />
        )}
      </Box>
    </Box>
  );
}
