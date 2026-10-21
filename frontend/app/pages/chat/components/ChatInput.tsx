/**
 * ChatInput — auto-resizing textarea with file attach & send button.
 *
 * With the Skills module on it also picks skills for the chat: the skills
 * button, or ``$`` followed by a skill's name; ``/skill`` opens the dialog
 * that makes a skill from the conversation.
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
  useEffect,
  useId,
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
import Typography from "@mui/material/Typography";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import SendIcon from "@mui/icons-material/Send";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import StopIcon from "@mui/icons-material/Stop";
import BuildIcon from "@mui/icons-material/Build";
import ExtensionIcon from "@mui/icons-material/Extension";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import SettingsIcon from "@mui/icons-material/Settings";
import ImageNotSupportedOutlinedIcon from "@mui/icons-material/ImageNotSupportedOutlined";
import { useTranslation } from "react-i18next";

import { isPictureFile, type ModelAlias } from "~/api/chatTypes";
import { filterSkills, mentionAt, type SkillSummary } from "~/api/skills";
import { MobileNavButton } from "~/components/MobileNavButton";
import { CHAT_NAV_ID, useChatShell } from "../ChatShellContext";
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
  /** Text to put back in the box ("edit and resend"); a new ``nonce`` puts it again. */
  draft?: { text: string; nonce: number } | null;
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
  draft = null,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const navigate = useNavigate();
  const skills = useChatSkills();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const phoneNav = useChatShell()?.phoneNav ?? null;
  const [text, setText] = useState("");
  useEffect(() => {
    if (draft) setText(draft.text);
  }, [draft]);
  const [files, setFiles] = useState<File[]>([]);
  // The chosen model as the picker lists it: a picture it would not get is
  // marked before sending (the gateway leaves it out and says so on the reply).
  const [model, setModel] = useState<ModelAlias | null>(null);
  const unseen = (file: File) => model?.vision === false && isPictureFile(file.name);
  const pictureHintId = useId();
  const [focused, setFocused] = useState(false);
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

  const skillsButton = skills.enabled && (
    <Tooltip title={t("skills.chat_button")}>
      <IconButton
        size={buttonSize}
        onClick={(e) => setSkillsAnchor(e.currentTarget)}
        color={skills.selected.length > 0 ? "primary" : "default"}
        aria-label={t("skills.chat_button")}
      >
        <ExtensionIcon fontSize="small" />
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
        (skills.enabled
          ? t("skills.chat_placeholder")
          : t("input_placeholder", {
              ns: "chat",
              defaultValue: "Type a message…",
            }))
      }
      value={text}
      onChange={(e) =>
        handleChange(e.target.value, (e.target as HTMLTextAreaElement).selectionStart)
      }
      onKeyDown={handleKeyDown}
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false);
        setTimeout(() => setMention(null), 150);
      }}
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
      onSelected={setModel}
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
          disabled={!canSend && !CREATE_COMMAND.test(text.trim())}
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

  const barSx = {
    bgcolor: theme.palette.background.paper,
    border: `1px solid ${theme.palette.divider}`,
    borderRadius: 1,
  };

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
          {files.map((f, i) =>
            unseen(f) ? (
              <Chip
                key={`${f.name}-${i}`}
                label={f.name}
                size="small"
                color="warning"
                variant="outlined"
                icon={<ImageNotSupportedOutlinedIcon />}
                aria-describedby={pictureHintId}
                onDelete={() => removeFile(i)}
              />
            ) : (
              <Chip
                key={`${f.name}-${i}`}
                label={f.name}
                size="small"
                onDelete={() => removeFile(i)}
              />
            ),
          )}
          {files.some(unseen) && (
            <Typography
              id={pictureHintId}
              variant="caption"
              color="warning.main"
              data-testid="picture-hint"
              sx={{ alignSelf: "center", px: 0.5 }}
            >
              {t("picture_not_seen", { ns: "chat", defaultValue: "This model can't see pictures" })}
            </Typography>
          )}
        </Box>
      )}

      {/* Input bar */}
      {isPhone ? (
        <Box
          ref={barRef}
          sx={{ ...barSx, display: "flex", flexDirection: "column", gap: 0.5, px: 1, pt: 1, pb: 0.5 }}
        >
          {textField}
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {toolsButton}
            {skillsButton}
            {attachButton}
            <Box sx={{ flex: 1, minWidth: 0, display: "flex" }}>{modelSelector}</Box>
            {sendButton}
          </Box>
        </Box>
      ) : (
        <Box
          ref={barRef}
          sx={{ ...barSx, display: "flex", alignItems: "flex-end", gap: 1, px: 1.5, py: 0.75 }}
        >
          {toolsButton}
          {skillsButton}
          {attachButton}
          {textField}
          {modelSelector}
          {sendButton}
        </Box>
      )}

      {/* That the answers come from an AI (EU AI Act Art. 50(1)), as the SDKs' panels say it */}
      <Typography
        variant="caption"
        color="text.secondary"
        align="center"
        dir="auto"
        data-testid="ai-notice"
        sx={{ mt: -0.5 }}
      >
        {t("chat.ai_notice")}
      </Typography>

      {/* $ picker */}
      <Popper
        open={pickerOpen}
        anchorEl={barRef.current}
        placement="top-start"
        sx={{ zIndex: theme.zIndex.modal }}
      >
        {/* On a phone the width of the screen, less its gutters: sized to
            its content, a long description would widen the page. */}
        <Paper
          elevation={6}
          sx={{
            mb: 1,
            minWidth: { xs: "calc(100vw - 16px)", sm: 320 },
            maxWidth: { xs: "calc(100vw - 16px)", sm: 480 },
          }}
        >
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
