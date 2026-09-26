/**
 * ChatWindow — active chat session with message history + streaming.
 */
import { useEffect, useRef, useState } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import { useTheme } from "@mui/material/styles";
import ReplayIcon from "@mui/icons-material/Replay";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import Avatar from "@mui/material/Avatar";
import type { ChatSession } from "~/api/chatTypes";
import type { UiLanguage } from "~/api/i18n";
import type { InitialMessageState } from "./ChatWelcome";
import { useChatSkills } from "../hooks/useChatSkills";
import { useChatStream } from "../hooks/useChatStream";
import MessageBubble from "./MessageBubble";
import MessageDebugPanel from "./MessageDebugPanel";
import ChatInput from "./ChatInput";
import ChatTopbar from "./ChatTopbar";

interface Props {
  sessionId: string;
  selectedModel: string;
  onModelChange: (alias: string) => void;
  onSessionCreated: (session: ChatSession) => void;
  onSessionUpdated: (session: ChatSession) => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
  languages: UiLanguage[];
  language: string;
  onLanguageChange: (code: string) => void;
  isSuperuser: boolean;
  canDebug?: boolean;
  onToolsToggle: () => void;
  toolsOpen: boolean;
  /** The chat's title, as the sidebar lists it. */
  title?: string | null;
  onRename?: (title: string) => Promise<void>;
}

export default function ChatWindow({
  sessionId,
  selectedModel,
  onModelChange,
  onSessionCreated,
  onSessionUpdated,
  onToggleSidebar,
  sidebarOpen,
  languages,
  language,
  onLanguageChange,
  isSuperuser,
  canDebug = false,
  onToolsToggle,
  toolsOpen,
  title,
  onRename,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const [debugTraceId, setDebugTraceId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const location = useLocation();
  const initialSentRef = useRef(false);
  const skills = useChatSkills();

  const {
    messages,
    streamingContent,
    streamingUsage,
    isStreaming,
    isLoading,
    error,
    getResponseMs,
    send,
    retry,
    stop,
    loadHistory,
  } = useChatStream({
    sessionId,
    selectedModel,
    onSessionCreated,
    onSessionUpdated,
  });

  // Load history OR auto-send initial message (mutually exclusive on mount).
  // A new session navigated from ChatWelcome carries an initialMessage in
  // location.state — send it immediately without loading history (there is
  // none yet). On page refresh the state is cleared so we fall through to
  // loadHistory which fetches persisted messages from the server.
  useEffect(() => {
    const state = location.state as InitialMessageState | undefined;
    if (state?.initialMessage) {
      // Sent once. A second run of this effect (React runs it twice in
      // development) must not fall through to loadHistory: that found the
      // stream this window had just started, "reconnected" to it, and read
      // the reply twice, alongside the send.
      if (!initialSentRef.current) {
        initialSentRef.current = true;
        send(
          state.initialMessage,
          state.initialModel,
          state.initialFiles,
          state.initialSkills,
        );
        // Clear navigation state so a page refresh won't re-send the message.
        // Preserve React Router's internal history keys while removing user state.
        const { usr: _usr, ...rest } = (window.history.state ?? {}) as Record<
          string,
          unknown
        >;
        window.history.replaceState(rest, "");
      }
      return;
    }
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Auto-scroll to bottom on new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  const handleSend = (text: string, files: File[]) => {
    send(text, selectedModel, files, skills.selected);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        position: "relative",
      }}
    >
      <ChatTopbar
        title={title || t("chat.untitled", { defaultValue: "Untitled chat" })}
        onRename={onRename}
        onToggleSidebar={onToggleSidebar}
        sidebarOpen={sidebarOpen}
        languages={languages}
        language={language}
        onLanguageChange={onLanguageChange}
        isSuperuser={isSuperuser}
        divider
      />

      {/* Messages area */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          pt: 2,
          pb: { xs: 9, sm: 2 },
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box sx={{ maxWidth: 800, mx: "auto", width: "100%", flex: 1 }}>
          {isLoading && (
            <Box
              sx={{
                display: "flex",
                justifyContent: "center",
                py: 4,
              }}
            >
              <CircularProgress size={28} />
            </Box>
          )}

          {/* Rendered messages */}
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              role={msg.role}
              content={msg.content}
              modelAlias={msg.model_alias}
              usage={msg.role === "assistant" ? (msg.usage ?? null) : null}
              replyTokens={msg.role === "assistant" ? msg.token_estimate : null}
              responseMs={
                msg.role === "assistant" ? getResponseMs(msg.id) : null
              }
              traceId={msg.trace_id}
              canDebug={canDebug}
              onDebugClick={setDebugTraceId}
            />
          ))}

          {/* Streaming assistant message */}
          {isStreaming && streamingContent && (
            <MessageBubble
              role="assistant"
              content={streamingContent}
              modelAlias={selectedModel || null}
              usage={streamingUsage}
              streaming
            />
          )}

          {/* Error bubble — shown after messages so it appears at the bottom */}
          {error && (
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "flex-start",
                gap: 1,
                mb: 1.5,
                px: 1,
              }}
            >
              <Avatar
                sx={{
                  width: 32,
                  height: 32,
                  mt: 0.5,
                  bgcolor: theme.palette.error.dark,
                  display: { xs: "none", sm: "flex" },
                }}
              >
                <ErrorOutlineIcon sx={{ fontSize: 18 }} />
              </Avatar>
              <Box
                sx={{
                  maxWidth: { xs: "100%", sm: "75%" },
                  minWidth: 60,
                  px: 2,
                  py: 1.5,
                  borderRadius: "4px 12px 12px 12px",
                  bgcolor:
                    theme.palette.mode === "dark"
                      ? theme.palette.background.paper
                      : theme.palette.grey[100],
                  border: `1px solid ${theme.palette.error.main}`,
                  color: theme.palette.text.primary,
                }}
              >
                <Typography
                  variant="caption"
                  sx={{
                    color: "error.main",
                    fontWeight: 600,
                    mb: 0.5,
                    display: "block",
                  }}
                >
                  {t("error_label", { ns: "chat", defaultValue: "Error" })}
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ mb: 1, wordBreak: "break-word" }}
                >
                  {error}
                </Typography>
                <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    startIcon={<ReplayIcon />}
                    onClick={retry}
                    sx={{ textTransform: "none" }}
                  >
                    {t("retry", { ns: "chat", defaultValue: "Retry" })}
                  </Button>
                </Box>
              </Box>
            </Box>
          )}

          {/* Streaming indicator when waiting for first token */}
          {isStreaming && !streamingContent && (
            <Box
              sx={{
                display: "flex",
                justifyContent: "flex-start",
                px: 1,
                mb: 1.5,
              }}
            >
              <Box
                sx={{
                  px: 2,
                  py: 1.5,
                  borderRadius: "4px 12px 12px 12px",
                  bgcolor:
                    theme.palette.mode === "dark"
                      ? theme.palette.background.paper
                      : theme.palette.grey[100],
                  border: `1px solid ${theme.palette.divider}`,
                }}
              >
                <CircularProgress size={16} />
              </Box>
            </Box>
          )}

          <div ref={bottomRef} />
        </Box>
      </Box>

      {/* Input */}
      <ChatInput
        onSend={handleSend}
        onStop={stop}
        selectedModel={selectedModel}
        onModelChange={onModelChange}
        streaming={isStreaming}
        onToolsToggle={onToolsToggle}
        toolsOpen={toolsOpen}
      />

      {/* Debug panel — only rendered when RBAC allows */}
      {canDebug && debugTraceId && (
        <MessageDebugPanel
          traceId={debugTraceId}
          open
          onClose={() => setDebugTraceId(null)}
        />
      )}
    </Box>
  );
}
