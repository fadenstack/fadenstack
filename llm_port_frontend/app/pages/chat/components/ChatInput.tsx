/**
 * ChatInput — auto-resizing textarea with file attach & send button.
 *
 * With the Skills module on it also picks skills for the chat: the skills
 * button, or ``$`` followed by a skill's name; ``/skill`` opens the dialog
 * that makes a skill from the conversation.
 */
import {
  type KeyboardEvent,
  type ChangeEvent,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router";
import Box from "@mui/material/Box";
import Checkbox from "@mui/material/Checkbox";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import InputBase from "@mui/material/InputBase";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import MenuList from "@mui/material/MenuList";
import Paper from "@mui/material/Paper";
import Popper from "@mui/material/Popper";
import Tooltip from "@mui/material/Tooltip";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import { useTheme } from "@mui/material/styles";
import SendIcon from "@mui/icons-material/Send";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import StopIcon from "@mui/icons-material/Stop";
import BuildIcon from "@mui/icons-material/Build";
import ExtensionIcon from "@mui/icons-material/Extension";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import SettingsIcon from "@mui/icons-material/Settings";
import { useTranslation } from "react-i18next";

import { filterSkills, mentionAt, type SkillSummary } from "~/api/skills";
import { useChatSkills } from "../hooks/useChatSkills";
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

/** ``/skill`` or ``/skills``, alone or followed by what the skill should do. */
const CREATE_COMMAND = /^\/skills?(?:\s+([\s\S]*))?$/i;
const PICKER_SIZE = 8;

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
  const navigate = useNavigate();
  const skills = useChatSkills();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [active, setActive] = useState(0);
  const [skillsAnchor, setSkillsAnchor] = useState<HTMLElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);
  const barRef = useRef<HTMLDivElement>(null);

  const matches = useMemo(
    () => (mention ? filterSkills(skills.usable, mention.query).slice(0, PICKER_SIZE) : []),
    [mention, skills.usable],
  );
  const pickerOpen = Boolean(mention) && matches.length > 0;
  const bySlug = useMemo(
    () => new Map(skills.usable.map((s) => [s.slug, s])),
    [skills.usable],
  );

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
    const command = skills.enabled && skills.canCreate ? CREATE_COMMAND.exec(text.trim()) : null;
    if (command) {
      skills.openCreate(command[1]?.trim() || undefined);
      setText("");
      setMention(null);
      return;
    }
    if (!canSend) return;
    onSend(text.trim(), files);
    setText("");
    setFiles([]);
    setMention(null);
    // Re-focus input after send
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [canSend, onSend, text, files, skills]);

  const pick = useCallback(
    (skill: SkillSummary) => {
      if (!mention) return;
      const caret = inputRef.current?.selectionStart ?? text.length;
      const next = text.slice(0, mention.start) + text.slice(caret);
      setText(next);
      setMention(null);
      if (!skills.selected.includes(skill.slug)) skills.toggle(skill.slug);
      setTimeout(() => {
        const el = inputRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(mention.start, mention.start);
      }, 0);
    },
    [mention, text, skills],
  );

  const handleChange = (value: string, caret: number | null) => {
    setText(value);
    if (!skills.enabled || skills.usable.length === 0) return;
    const found = mentionAt(value, caret ?? value.length);
    setMention(found);
    setActive(0);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (pickerOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => (i + 1) % matches.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => (i - 1 + matches.length) % matches.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pick(matches[Math.min(active, matches.length - 1)]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMention(null);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
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

  return (
    <Box
      sx={{
        px: 2,
        pb: 2,
        pt: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        maxWidth: 800,
        mx: "auto",
        width: "100%",
      }}
    >
      {/* Picked skills and attached files */}
      {(files.length > 0 || skills.selected.length > 0) && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
          {skills.selected.map((slug) => (
            <Tooltip key={`skill-${slug}`} title={bySlug.get(slug)?.description ?? ""}>
              <Chip
                size="small"
                color="primary"
                variant="outlined"
                icon={<ExtensionIcon />}
                label={bySlug.get(slug)?.name ?? `$${slug}`}
                onDelete={() => skills.remove(slug)}
              />
            </Tooltip>
          ))}
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
      <Box
        ref={barRef}
        sx={{
          display: "flex",
          alignItems: "flex-end",
          gap: 1,
          bgcolor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: 3,
          px: 1.5,
          py: 0.75,
        }}
      >
        {/* Tools button */}
        {onToolsToggle && (
          <Tooltip title={t("tools.panel.title", { defaultValue: "Tools" })}>
            <IconButton
              size="small"
              onClick={onToolsToggle}
              color={toolsOpen ? "primary" : "default"}
            >
              <BuildIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}

        {/* Skills button */}
        {skills.enabled && (
          <Tooltip title={t("skills.chat_button")}>
            <IconButton
              size="small"
              onClick={(e) => setSkillsAnchor(e.currentTarget)}
              color={skills.selected.length > 0 ? "primary" : "default"}
              aria-label={t("skills.chat_button")}
            >
              <ExtensionIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}

        {/* Attach button */}
        <Tooltip title={t("chat.attach_file", { defaultValue: "Attach file" })}>
          <IconButton
            size="small"
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

        {/* Textarea */}
        <InputBase
          inputRef={inputRef}
          multiline
          maxRows={8}
          fullWidth
          placeholder={
            placeholder ??
            (skills.enabled
              ? t("skills.chat_placeholder")
              : t("input_placeholder", {
                  ns: "chat",
                  defaultValue: "Type a message…",
                }))
          }
          value={text}
          onChange={(e) =>
            handleChange(
              e.target.value,
              (e.target as HTMLTextAreaElement).selectionStart,
            )
          }
          onKeyDown={handleKeyDown}
          onBlur={() => setTimeout(() => setMention(null), 150)}
          disabled={disabled}
          autoFocus={autoFocus}
          sx={{ fontSize: "0.95rem", py: 0.5 }}
        />

        {/* Model selector */}
        <ModelSelector
          value={selectedModel}
          onChange={onModelChange}
          size="small"
        />

        {/* Send / Stop button */}
        {streaming ? (
          <Tooltip title={t("chat.stop", { defaultValue: "Stop" })}>
            <IconButton size="small" color="error" onClick={onStop}>
              <StopIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        ) : (
          <Tooltip title={t("chat.send", { defaultValue: "Send" })}>
            <span>
              <IconButton
                size="small"
                color="primary"
                onClick={handleSend}
                disabled={!canSend && !CREATE_COMMAND.test(text.trim())}
              >
                {disabled ? (
                  <CircularProgress size={18} />
                ) : (
                  <SendIcon fontSize="small" />
                )}
              </IconButton>
            </span>
          </Tooltip>
        )}
      </Box>

      {/* $ picker */}
      <Popper
        open={pickerOpen}
        anchorEl={barRef.current}
        placement="top-start"
        sx={{ zIndex: theme.zIndex.modal }}
      >
        <Paper elevation={6} sx={{ mb: 1, minWidth: 320, maxWidth: 480 }}>
          <MenuList dense aria-label={t("skills.chat_picker")}>
            {matches.map((s, i) => (
              <MenuItem
                key={s.id}
                selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(s);
                }}
              >
                <ListItemIcon>
                  <ExtensionIcon fontSize="small" />
                </ListItemIcon>
                <ListItemText
                  primary={`${s.name}  ·  $${s.slug}`}
                  secondary={s.description}
                  slotProps={{ secondary: { noWrap: true } }}
                />
              </MenuItem>
            ))}
          </MenuList>
        </Paper>
      </Popper>

      {/* Skills menu */}
      <Menu
        anchorEl={skillsAnchor}
        open={Boolean(skillsAnchor)}
        onClose={() => setSkillsAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { minWidth: 300, maxWidth: 420, maxHeight: 440 } } }}
      >
        <MenuItem disabled dense sx={{ opacity: "1 !important" }}>
          <ListItemText
            primary={t("skills.chat_menu_title")}
            secondary={t("skills.chat_menu_hint")}
          />
        </MenuItem>
        {skills.usable.length === 0 && (
          <MenuItem disabled dense>
            <ListItemText secondary={t("skills.chat_none")} />
          </MenuItem>
        )}
        {skills.usable.map((s) => (
          <MenuItem key={s.id} dense onClick={() => skills.toggle(s.slug)}>
            <ListItemIcon>
              <Checkbox
                edge="start"
                size="small"
                checked={skills.selected.includes(s.slug)}
                tabIndex={-1}
                disableRipple
              />
            </ListItemIcon>
            <ListItemText
              primary={s.name}
              secondary={`$${s.slug}`}
              slotProps={{ primary: { noWrap: true } }}
            />
          </MenuItem>
        ))}
        <Divider />
        {skills.canCreate && (
          <MenuItem
            onClick={() => {
              setSkillsAnchor(null);
              skills.openCreate();
            }}
          >
            <ListItemIcon>
              <AutoAwesomeIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary={t("skills.chat_create")} secondary="/skill" />
          </MenuItem>
        )}
        <MenuItem
          onClick={() => {
            setSkillsAnchor(null);
            navigate("/skills");
          }}
        >
          <ListItemIcon>
            <SettingsIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary={t("skills.chat_manage")} />
        </MenuItem>
      </Menu>
    </Box>
  );
}
