/**
 * ChatWindow — active chat session with message history + streaming.
 */
import { Fragment, useEffect, useRef, useState } from "react";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import { useTheme } from "@mui/material/styles";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import type { ChatSession } from "~/api/chatTypes";
import type { UiLanguage } from "~/api/i18n";
import type { InitialMessageState } from "./ChatWelcome";
import { useChatSkills } from "../hooks/useChatSkills";
import { useChatStream } from "../hooks/useChatStream";
import FailedReply from "./FailedReply";
import MessageBubble from "./MessageBubble";
import ThinkingBlock from "./ThinkingBlock";
import MessageDebugPanel from "./MessageDebugPanel";
import ChatInput from "./ChatInput";
import ChatTopbar from "./ChatTopbar";

interface Props {
  sessionId: string;
  selectedModel: string;
  onModelChange: (alias: string) => void;
  onSessionCreated: (session: ChatSession) => void;
  onSessionUpdated: (session: ChatSession) => void;
  /** Whether a reply is being written in this chat, for the sidebar. */
  onWritingChange?: (sessionId: string, writing: boolean) => void;
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
  onWritingChange,
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
    streamingReasoning,
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

  // The sidebar marks the chat while its reply is written; it goes on when
  // the user moves to another chat.
  useEffect(() => {
    if (isStreaming && sessionId) onWritingChange?.(sessionId, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming, sessionId]);

  // Back to the tab, or back online: a reply that was being written is read
  // again (a phone suspends a page and its connection).
  useEffect(() => {
    const catchUp = () => {
      if (document.visibilityState !== "visible" || isStreaming) return;
      if (messages[messages.length - 1]?.status === "running") void loadHistory();
    };
    document.addEventListener("visibilitychange", catchUp);
    window.addEventListener("online", catchUp);
    return () => {
      document.removeEventListener("visibilitychange", catchUp);
      window.removeEventListener("online", catchUp);
    };
  }, [isStreaming, messages, loadHistory]);

  // Auto-scroll to bottom on new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent, streamingReasoning]);

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
          {messages.map((msg, i) => {
            // The chat's last reply, once it failed or stopped, is tried again from here.
            const onRetry = i === messages.length - 1 && !isStreaming ? retry : undefined;
            const failed = msg.status === "failed" || msg.status === "interrupted";
            const bubble = (!failed || msg.content) && (
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
                reasoning={msg.role === "assistant" ? msg.reasoning : null}
                reasoningMs={msg.role === "assistant" ? msg.reasoning_ms : null}
                streaming={msg.status === "running"}
                stopped={msg.status === "stopped"}
                onRetry={msg.status === "stopped" ? onRetry : undefined}
              />
            );
            if (!failed) return bubble;
            return (
              <Fragment key={msg.id}>
                {bubble}
                <FailedReply
                  message={
                    msg.status === "interrupted"
                      ? t("turn_interrupted", {
                          ns: "chat",
                          defaultValue: "The server restarted while this reply was being written.",
                        })
                      : msg.error ||
                        t("turn_failed", { ns: "chat", defaultValue: "The reply failed." })
                  }
                  onRetry={onRetry}
                />
              </Fragment>
            );
          })}

          {/* Streaming assistant message */}
          {isStreaming && streamingContent && (
            <MessageBubble
              role="assistant"
              content={streamingContent}
              modelAlias={selectedModel || null}
              usage={streamingUsage}
              streaming
              reasoning={streamingReasoning || null}
            />
          )}

          {/* An error only this page saw: no reply was stored for it */}
          {error && <FailedReply message={error} onRetry={retry} />}

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
                  // Room for the thinking to show, as wide as a reply may be.
                  ...(streamingReasoning && { maxWidth: { xs: "100%", sm: "75%" }, minWidth: 220 }),
                }}
              >
                {streamingReasoning ? (
                  <ThinkingBlock text={streamingReasoning} live />
                ) : (
                  <CircularProgress size={16} />
                )}
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
