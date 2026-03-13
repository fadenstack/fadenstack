/**
 * ChatWelcome — centred welcome when no session is active.
 *
 * On first message, creates a session and navigates to it, passing the
 * initial message via React Router state so ChatWindow can send it.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import LightModeIcon from "@mui/icons-material/LightMode";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import { useTranslation } from "react-i18next";

import type { ChatSession } from "~/api/chatTypes";
import { chatApi } from "~/api/chatClient";
import { useThemeMode } from "~/theme-mode";
import ChatInput from "./ChatInput";

interface Props {
  selectedModel: string;
  onModelChange: (alias: string) => void;
  onSessionCreated: (
    session: ChatSession,
    initialState?: InitialMessageState,
  ) => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
}

/** Navigation state passed to ChatWindow for first-message auto-send. */
export interface InitialMessageState {
  initialMessage: string;
  initialModel: string;
  initialFiles?: File[];
}

const SUGGESTIONS = [
  "Explain quantum computing in simple terms",
  "Write a Python function to merge two sorted lists",
  "Summarize the key points of a research paper",
  "Help me brainstorm ideas for a startup",
];

export default function ChatWelcome({
  selectedModel,
  onModelChange,
  onSessionCreated,
  onToggleSidebar,
  sidebarOpen,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const { mode, toggleMode } = useThemeMode();
  const [sending, setSending] = useState(false);

  const handleSend = async (text: string, files: File[]) => {
    if (sending) return;
    setSending(true);
    try {
      const session = await chatApi.createSession({
        title: text.slice(0, 100),
      });
      onSessionCreated(session, {
        initialMessage: text,
        initialModel: selectedModel,
        initialFiles: files.length > 0 ? files : undefined,
      });
    } catch {
      setSending(false);
    }
  };

  const handleSuggestion = (text: string) => {
    handleSend(text, []);
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
      {/* Top bar with sidebar toggle */}
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
        <Box>
          {!sidebarOpen && (
            <IconButton size="small" onClick={onToggleSidebar}>
              <MenuIcon />
            </IconButton>
          )}
        </Box>
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

      {/* Centred hero area */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          px: 3,
          pb: 10,
        }}
      >
        <AutoAwesomeIcon
          sx={{ fontSize: 48, color: theme.palette.primary.main, mb: 2 }}
        />
        <Typography variant="h4" fontWeight={700} gutterBottom>
          {t("chat.welcome_title", { defaultValue: "How can I help you?" })}
        </Typography>
        <Typography
          variant="body1"
          color="text.secondary"
          sx={{ mb: 4, maxWidth: 480, textAlign: "center" }}
        >
          {t("chat.welcome_subtitle", {
            defaultValue:
              "Start a conversation, upload a file, or try one of these suggestions.",
          })}
        </Typography>

        {/* Suggestion chips */}
        <Box
          sx={{
            display: "flex",
            flexWrap: "wrap",
            gap: 1,
            justifyContent: "center",
            maxWidth: 600,
          }}
        >
          {SUGGESTIONS.map((s) => (
            <Chip
              key={s}
              label={s}
              variant="outlined"
              onClick={() => handleSuggestion(s)}
              sx={{
                cursor: "pointer",
                "&:hover": {
                  bgcolor: theme.palette.action.hover,
                },
              }}
            />
          ))}
        </Box>
      </Box>

      {/* Bottom-anchored input */}
      <ChatInput
        onSend={handleSend}
        selectedModel={selectedModel}
        onModelChange={onModelChange}
        streaming={sending}
      />
    </Box>
  );
}
