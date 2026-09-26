/**
 * ChatInput — auto-resizing textarea with file attach & send button.
 *
 * On a phone the text gets the whole first row and the actions sit under it
 * (the model picker beside the text had left it a word wide); the text is
 * 16 px so iOS does not zoom in on focus; and the navigation button floats
 * above the composer, out of the way while the keyboard is up.
 */
import {
  type KeyboardEvent,
  type ChangeEvent,
  useCallback,
  useRef,
  useState,
} from "react";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import InputBase from "@mui/material/InputBase";
import Tooltip from "@mui/material/Tooltip";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import SendIcon from "@mui/icons-material/Send";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import StopIcon from "@mui/icons-material/Stop";
import BuildIcon from "@mui/icons-material/Build";
import { useTranslation } from "react-i18next";

import { MobileNavButton } from "~/components/MobileNavButton";
import { CHAT_NAV_ID, useChatShell } from "../ChatShellContext";
import ModelSelector from "./ModelSelector";

interface Props {
  onSend: (text: string, files: File[]) => void;
  onStop?: () => void;
  selectedModel: string;
  onModelChange: (alias: string) => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
  onToolsToggle?: () => void;
  toolsOpen?: boolean;
}

export default function ChatInput({
  onSend,
  onStop,
  selectedModel,
  onModelChange,
  disabled = false,
  streaming = false,
  placeholder,
  autoFocus = true,
  onToolsToggle,
  toolsOpen = false,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const phoneNav = useChatShell()?.phoneNav ?? null;
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [focused, setFocused] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // A model has to be chosen before there is anywhere to send to.  The list
  // is fetched, and the selector auto-picks the first entry once it arrives,
  // so for the first second after the page opens ``selectedModel`` is empty.
  // Sending in that window used to reach the stream hook and come back as
  // "Please select a model before sending a message." -- while the picker
  // was, by then, showing a model.  Blocking the send is the honest version:
  // the button is visibly unavailable until there is something to send to.
  const canSend =
    text.trim().length > 0 && !disabled && !streaming && selectedModel !== "";

  const handleSend = useCallback(() => {
    if (!canSend) return;
    onSend(text.trim(), files);
    setText("");
    setFiles([]);
    // Re-focus input after send
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [canSend, onSend, text, files]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // On a phone Enter is a new line; the send button sends.
    if (e.key === "Enter" && !e.shiftKey && !isPhone) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (selected) {
      setFiles((prev) => [...prev, ...Array.from(selected)]);
    }
    // Reset so same file can be selected again
    e.target.value = "";
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const buttonSize = isPhone ? "medium" : "small";

  const toolsButton = onToolsToggle && (
    <Tooltip title={t("tools.panel.title", { defaultValue: "Tools" })}>
      <IconButton
        size={buttonSize}
        onClick={onToolsToggle}
        color={toolsOpen ? "primary" : "default"}
      >
        <BuildIcon fontSize="small" />
      </IconButton>
    </Tooltip>
  );

  const attachButton = (
    <>
      <Tooltip title={t("chat.attach_file", { defaultValue: "Attach file" })}>
        <IconButton
          size={buttonSize}
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || streaming}
        >
          <AttachFileIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <input
        ref={fileInputRef}
        type="file"
        hidden
        multiple
        onChange={handleFileSelect}
      />
    </>
  );

  const textField = (
    <InputBase
      inputRef={inputRef}
      multiline
      maxRows={isPhone ? 6 : 8}
      fullWidth
      placeholder={
        placeholder ??
        t("input_placeholder", {
          ns: "chat",
          defaultValue: "Type a message…",
        })
      }
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={handleKeyDown}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      disabled={disabled}
      // A phone would open its keyboard over the page on arrival.
      autoFocus={autoFocus && !isPhone}
      sx={{ fontSize: isPhone ? "1rem" : "0.95rem", py: 0.5, px: isPhone ? 0.5 : 0 }}
    />
  );

  const modelSelector = (
    <ModelSelector
      value={selectedModel}
      onChange={onModelChange}
      size="small"
      compact={isPhone}
    />
  );

  const sendButton = streaming ? (
    <Tooltip title={t("chat.stop", { defaultValue: "Stop" })}>
      <IconButton size={buttonSize} color="error" onClick={onStop}>
        <StopIcon fontSize="small" />
      </IconButton>
    </Tooltip>
  ) : (
    <Tooltip title={t("chat.send", { defaultValue: "Send" })}>
      <span>
        <IconButton
          size={buttonSize}
          color="primary"
          onClick={handleSend}
          disabled={!canSend}
          aria-label={t("chat.send", { defaultValue: "Send" })}
          sx={
            isPhone
              ? {
                  bgcolor: "primary.main",
                  color: "primary.contrastText",
                  "&:hover": { bgcolor: "primary.dark" },
                  "&.Mui-disabled": { bgcolor: "action.disabledBackground" },
                }
              : undefined
          }
        >
          {disabled ? (
            <CircularProgress size={18} />
          ) : (
            <SendIcon fontSize="small" />
          )}
        </IconButton>
      </span>
    </Tooltip>
  );

  return (
    <Box
      sx={{
        px: { xs: 1, sm: 2 },
        pb: { xs: "calc(8px + env(safe-area-inset-bottom, 0px))", sm: 2 },
        pt: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        maxWidth: 800,
        mx: "auto",
        width: "100%",
        position: "relative",
      }}
    >
      {phoneNav && (
        <MobileNavButton
          hand={phoneNav.hand}
          open={phoneNav.open}
          onOpen={phoneNav.onOpen}
          controls={CHAT_NAV_ID}
          visible={!focused}
          sx={{ position: "absolute", bottom: "calc(100% + 8px)" }}
        />
      )}

      {/* File chips */}
      {files.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
          {files.map((f, i) => (
            <Chip
              key={`${f.name}-${i}`}
              label={f.name}
              size="small"
              onDelete={() => removeFile(i)}
            />
          ))}
        </Box>
      )}

      {/* Input bar */}
      {isPhone ? (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
            bgcolor: theme.palette.background.paper,
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: 1,
            px: 1,
            pt: 1,
            pb: 0.5,
          }}
        >
          {textField}
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {toolsButton}
            {attachButton}
            <Box sx={{ flex: 1, minWidth: 0, display: "flex" }}>{modelSelector}</Box>
            {sendButton}
          </Box>
        </Box>
      ) : (
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-end",
            gap: 1,
            bgcolor: theme.palette.background.paper,
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: 1,
            px: 1.5,
            py: 0.75,
          }}
        >
          {toolsButton}
          {attachButton}
          {textField}
          {modelSelector}
          {sendButton}
        </Box>
      )}
    </Box>
  );
}
