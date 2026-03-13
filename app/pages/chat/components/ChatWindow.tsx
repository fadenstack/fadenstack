/**
 * ChatWindow — active chat session with message history + streaming.
 */
import { useEffect, useRef } from "react";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import LightModeIcon from "@mui/icons-material/LightMode";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import type { ChatSession, TokenUsage } from "~/api/chatTypes";
import type { InitialMessageState } from "./ChatWelcome";
import { useThemeMode } from "~/theme-mode";
import { useChatStream } from "../hooks/useChatStream";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";

interface Props {
  sessionId: string;
  selectedModel: string;
  onModelChange: (alias: string) => void;
  onSessionCreated: (session: ChatSession) => void;
  onSessionUpdated: (session: ChatSession) => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
}

export default function ChatWindow({
  sessionId,
  selectedModel,
  onModelChange,
  onSessionCreated,
  onSessionUpdated,
  onToggleSidebar,
  sidebarOpen,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const { mode, toggleMode } = useThemeMode();
  const bottomRef = useRef<HTMLDivElement>(null);
  const location = useLocation();
  const initialSentRef = useRef(false);

  const {
    messages,
    streamingContent,
    streamingUsage,
    isStreaming,
    isLoading,
    error,
    getResponseMs,
    send,
    stop,
    loadHistory,
  } = useChatStream({
    sessionId,
    onSessionCreated,
    onSessionUpdated,
  });

  // Load history when session changes
  useEffect(() => {
    loadHistory();
  }, [sessionId, loadHistory]);

  // Auto-send initial message passed from ChatWelcome via navigation state
  useEffect(() => {
    const state = location.state as InitialMessageState | undefined;
    if (state?.initialMessage && !initialSentRef.current) {
      initialSentRef.current = true;
      send(state.initialMessage, state.initialModel, state.initialFiles);
    }
  }, [location.state, send]);

  // Auto-scroll to bottom on new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  const handleSend = (text: string, files: File[]) => {
    send(text, selectedModel, files);
  };

  // Parse token_estimate from message for display
  const usageFromMessage = (
    tokenEstimate: number | null,
    modelAlias: string | null,
  ): TokenUsage | null => {
    if (!tokenEstimate) return null;
    return {
      prompt_tokens: 0,
      completion_tokens: tokenEstimate,
      total_tokens: tokenEstimate,
    };
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
      {/* Top bar */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 1.5,
          py: 1,
          minHeight: 56,
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        {!sidebarOpen && (
          <IconButton size="small" onClick={onToggleSidebar} sx={{ mr: 1 }}>
            <MenuIcon />
          </IconButton>
        )}
        <Typography variant="body1" fontWeight={600} noWrap sx={{ flex: 1 }}>
          {t("chat.conversation", { defaultValue: "Conversation" })}
        </Typography>
        <Tooltip title={mode === "dark" ? "Light mode" : "Dark mode"}>
          <IconButton size="small" onClick={toggleMode}>
            {mode === "dark" ? (
              <LightModeIcon fontSize="small" />
            ) : (
              <DarkModeIcon fontSize="small" />
            )}
          </IconButton>
        </Tooltip>
      </Box>

      {/* Messages area */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          py: 2,
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

          {error && (
            <Alert severity="error" sx={{ mx: 2, mb: 2 }}>
              {error}
            </Alert>
          )}

          {/* Rendered messages */}
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              role={msg.role}
              content={msg.content}
              modelAlias={msg.model_alias}
              usage={
                msg.role === "assistant"
                  ? usageFromMessage(msg.token_estimate, msg.model_alias)
                  : null
              }
              responseMs={
                msg.role === "assistant" ? getResponseMs(msg.id) : null
              }
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
                  borderRadius: 3,
                  bgcolor: theme.palette.background.paper,
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
      />
    </Box>
  );
}
