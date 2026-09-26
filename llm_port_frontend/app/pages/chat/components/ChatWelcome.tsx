/**
 * ChatWelcome — centred welcome when no session is active.
 *
 * On first message, creates a session and navigates to it, passing the
 * initial message via React Router state so ChatWindow can send it.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Chip from "@mui/material/Chip";
import { useTheme } from "@mui/material/styles";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import { useTranslation } from "react-i18next";

import type { ChatSession } from "~/api/chatTypes";
import type { UiLanguage } from "~/api/i18n";
import { chatApi } from "~/api/chatClient";
import ChatInput from "./ChatInput";
import ChatTopbar from "./ChatTopbar";

interface Props {
  selectedModel: string;
  onModelChange: (alias: string) => void;
  onSessionCreated: (
    session: ChatSession,
    initialState?: InitialMessageState,
  ) => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
  languages: UiLanguage[];
  language: string;
  onLanguageChange: (code: string) => void;
  isSuperuser: boolean;
  onToolsToggle?: () => void;
  toolsOpen?: boolean;
}

/** Navigation state passed to ChatWindow for first-message auto-send. */
export interface InitialMessageState {
  initialMessage: string;
  initialModel: string;
  initialFiles?: File[];
}

export default function ChatWelcome({
  selectedModel,
  onModelChange,
  onSessionCreated,
  onToggleSidebar,
  sidebarOpen,
  languages,
  language,
  onLanguageChange,
  isSuperuser,
  onToolsToggle,
  toolsOpen,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const [sending, setSending] = useState(false);

  const suggestions = [
    t("suggestion_1", {
      ns: "chat",
      defaultValue: "Explain quantum computing in simple terms",
    }),
    t("suggestion_2", {
      ns: "chat",
      defaultValue: "Write a Python function to merge two sorted lists",
    }),
    t("suggestion_3", {
      ns: "chat",
      defaultValue: "Summarize the key points of a research paper",
    }),
    t("suggestion_4", {
      ns: "chat",
      defaultValue: "Help me brainstorm ideas for a startup",
    }),
  ];

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
      <ChatTopbar
        onToggleSidebar={onToggleSidebar}
        sidebarOpen={sidebarOpen}
        languages={languages}
        language={language}
        onLanguageChange={onLanguageChange}
        isSuperuser={isSuperuser}
      />

      {/* Centred hero area */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          px: { xs: 2, sm: 3 },
          pb: { xs: 4, sm: 10 },
        }}
      >
        <AutoAwesomeIcon
          sx={{ fontSize: 48, color: theme.palette.primary.main, mb: 2 }}
        />
        <Typography variant="h4" fontWeight={700} gutterBottom>
          {t("welcome_title", {
            ns: "chat",
            defaultValue: "How can I help you?",
          })}
        </Typography>
        <Typography
          variant="body1"
          color="text.secondary"
          sx={{ mb: 4, maxWidth: 480, textAlign: "center" }}
        >
          {t("welcome_subtitle", {
            ns: "chat",
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
          {suggestions.map((s) => (
            <Chip
              key={s}
              label={s}
              variant="outlined"
              onClick={() => handleSuggestion(s)}
              sx={{
                cursor: "pointer",
                // A long suggestion wraps on a narrow screen rather than
                // running off it.
                height: "auto",
                minHeight: 32,
                py: 0.5,
                "& .MuiChip-label": { whiteSpace: "normal", textAlign: "center" },
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
        onToolsToggle={onToolsToggle}
        toolsOpen={toolsOpen}
      />
    </Box>
  );
}
