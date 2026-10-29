/**
 * ChatWindow — active chat session with message history + streaming.
 */
import { Fragment, useEffect, useRef, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Fab from "@mui/material/Fab";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import type { ChatSession } from "~/api/chatTypes";
import type { UiLanguage } from "~/api/i18n";
import { fmtWhen } from "~/components/usage/usageFormat";
import type { InitialMessageState } from "./ChatWelcome";
import { useChatSkills } from "../hooks/useChatSkills";
import { useChatStream } from "../hooks/useChatStream";
import FailedReply from "./FailedReply";
import MessageBubble from "./MessageBubble";
import ThinkingBlock from "./ThinkingBlock";
import MessageDebugPanel from "./MessageDebugPanel";
import ChatInput from "./ChatInput";
import ChatTopbar from "./ChatTopbar";
import { followScroll, type Follower } from "../followScroll";

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
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const [debugTraceId, setDebugTraceId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ text: string; nonce: number } | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
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
    errorInfo,
    pendingApproval,
    answerApproval,
    toolNotices,
    streamingNotes,
    getReplyNotes,
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

  // The view follows a reply as it is written -- until the user scrolls up to
  // read (followScroll.ts: read from the wheel, touch and keys themselves; a
  // trackpad's small steps used to be undone by the next token). Back at the
  // bottom, or sending, it follows again.
  const followerRef = useRef<Follower | null>(null);
  const [following, setFollowing] = useState(true);
  useEffect(() => {
    const box = scrollerRef.current;
    if (!box) return;
    const follower = followScroll(box, setFollowing);
    followerRef.current = follower;
    follower.follow();
    return () => {
      follower.dispose();
      followerRef.current = null;
    };
  }, []);

  // Another chat opens at its latest message.
  useEffect(() => {
    followerRef.current?.follow();
  }, [sessionId]);

  useEffect(() => {
    followerRef.current?.keepUp();
  }, [messages, streamingContent, streamingReasoning, toolNotices, streamingNotes, pendingApproval, error]);

  const jumpToLatest = () => {
    followerRef.current?.follow();
  };

  const handleSend = (text: string, files: File[]) => {
    jumpToLatest();
    send(text, selectedModel, files, skills.selected);
  };

  const handleRetry = () => {
    jumpToLatest();
    return retry();
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
        ref={scrollerRef}
        data-testid="chat-messages"
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
            const onRetry = i === messages.length - 1 && !isStreaming ? handleRetry : undefined;
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
                status={msg.status}
                notice={msg.notice}
                notes={msg.role === "assistant" ? getReplyNotes(msg.id) : undefined}
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
              notes={streamingNotes}
            />
          )}

          {/* Tools refused while this answer streams (tool mode, a rule) */}
          {isStreaming && toolNotices.length > 0 && (
            <Box sx={{ px: 1, mb: 1 }} data-testid="tool-notices">
              {toolNotices.map((notice, i) => (
                <Typography key={i} variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  {t("tool_refused", { ns: "chat", defaultValue: "Not run" })}: {notice}
                </Typography>
              ))}
            </Box>
          )}

          {/* A tool waits for the user: the answer goes on once they decide */}
          {pendingApproval && (
            <Box
              data-testid="tool-approval"
              sx={{
                mx: 1,
                mb: 1.5,
                px: 2,
                py: 1.5,
                borderRadius: 2,
                border: `1px solid ${theme.palette.warning.main}`,
                bgcolor: theme.palette.background.paper,
              }}
            >
              <Typography variant="caption" sx={{ fontWeight: 600, display: "block", color: "warning.main" }}>
                {t("approval_title", {
                  ns: "chat",
                  defaultValue: "{{tool}} wants to run",
                  tool: pendingApproval.toolName || t("approval_a_tool", { ns: "chat", defaultValue: "A tool" }),
                })}
              </Typography>
              <Typography variant="body2" sx={{ mb: 1 }}>
                {pendingApproval.message}
              </Typography>
              <Box sx={{ display: "flex", gap: 1, justifyContent: "flex-end" }}>
                <Button size="small" variant="outlined" onClick={() => void answerApproval(false)} sx={{ textTransform: "none" }}>
                  {t("approval_decline", { ns: "chat", defaultValue: "Decline" })}
                </Button>
                <Button size="small" variant="contained" onClick={() => void answerApproval(true)} sx={{ textTransform: "none" }}>
                  {t("approval_approve", { ns: "chat", defaultValue: "Approve" })}
                </Button>
              </Box>
            </Box>
          )}

          {/* A refused prompt: its notice is in the history; offer to edit it. */}
          {errorInfo?.kind === "blocked" && (
            <Box sx={{ display: "flex", justifyContent: "flex-end", px: 1, mb: 1.5 }}>
              <Button
                size="small"
                variant="outlined"
                color="info"
                onClick={() => setDraft({ text: errorInfo.text ?? "", nonce: Date.now() })}
                sx={{ textTransform: "none" }}
              >
                {t("edit_and_resend", { ns: "chat", defaultValue: "Edit and resend" })}
              </Button>
            </Box>
          )}

          {/* An error only this page saw: no reply was stored for it */}
          {error && errorInfo?.kind !== "blocked" && (
            <FailedReply
              message={
                errorInfo?.kind === "rate_limit" && errorInfo.retryAfter
                  ? `${error} ${t("retry_after", {
                      ns: "chat",
                      defaultValue: "Try again in {{seconds}} s.",
                      seconds: errorInfo.retryAfter,
                    })}`
                  : errorInfo?.kind === "budget" && errorInfo.retryAfter
                    ? `${error} ${t("budgets.refused.until", {
                        when: fmtWhen(new Date(Date.now() + errorInfo.retryAfter * 1000).toISOString(), i18n.language),
                      })}`
                    : error
              }
              label={
                errorInfo?.kind === "rate_limit"
                  ? t("rate_limited_label", { ns: "chat", defaultValue: "Rate limit reached" })
                  : errorInfo?.kind === "budget"
                    ? t("budgets.refused.label")
                    : undefined
              }
              onRetry={handleRetry}
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

          {/* Scrolled up: the way back down, kept in view at the bottom edge. */}
          {!following && (
            <Box
              sx={{
                position: "sticky",
                bottom: 12,
                height: 0,
                display: "flex",
                justifyContent: "center",
                overflow: "visible",
              }}
            >
              <Tooltip title={t("jump_to_latest", { ns: "chat", defaultValue: "Jump to the latest message" })}>
                <Fab
                  size="small"
                  onClick={jumpToLatest}
                  aria-label={t("jump_to_latest", { ns: "chat", defaultValue: "Jump to the latest message" })}
                  data-testid="jump-to-latest"
                  sx={{
                    transform: "translateY(-100%)",
                    bgcolor: "background.paper",
                    color: "text.primary",
                    border: `1px solid ${theme.palette.divider}`,
                    boxShadow: 2,
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <KeyboardArrowDownIcon />
                </Fab>
              </Tooltip>
            </Box>
          )}
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
        draft={draft}
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
