/**
 * ProjectFiles -- the files of a chat project, in its settings.
 *
 * A project's files go with every chat of the project: the gateway reads
 * their text into the model's context. The list shows each file's size and
 * whether its text could be read; a file is added from the button and is
 * removed, after a question, at once (the file itself is deleted).
 */
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import { useTranslation } from "react-i18next";

import { chatApi, isNotFound } from "~/api/chatClient";
import type { ChatAttachment } from "~/api/chatTypes";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { fmtBytes } from "~/components/dashboard/format";

interface Props {
  projectId: string;
}

/** A file on its way up, or one that was refused. */
interface Upload {
  key: number;
  name: string;
  failed: string | null;
}

const reason = (err: unknown) => (err instanceof Error ? err.message : String(err));

export default function ProjectFiles({ projectId }: Props) {
  const { t, i18n } = useTranslation();
  const [files, setFiles] = useState<ChatAttachment[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [removeTarget, setRemoveTarget] = useState<ChatAttachment | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);
  // The project the dialog shows now: an answer for another is dropped.
  const current = useRef(projectId);
  current.current = projectId;

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await chatApi.listProjectAttachments(projectId);
      if (current.current === projectId) setFiles(list);
    } catch (err) {
      if (current.current === projectId) setLoadError(reason(err));
    } finally {
      if (current.current === projectId) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    setFiles([]);
    setUploads([]);
    void load();
  }, [load]);

  const upload = async (file: File) => {
    const key = nextKey.current++;
    setUploads((prev) => [...prev, { key, name: file.name, failed: null }]);
    try {
      const done = await chatApi.uploadProjectAttachment(projectId, file);
      if (current.current !== projectId) return;
      setFiles((prev) => [...prev, done.attachment]);
      setUploads((prev) => prev.filter((u) => u.key !== key));
    } catch (err) {
      setUploads((prev) => prev.map((u) => (u.key === key ? { ...u, failed: reason(err) } : u)));
    }
  };

  const handlePick = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    // So the same file can be picked again.
    e.target.value = "";
    for (const file of picked) void upload(file);
  };

  const confirmRemove = async () => {
    const target = removeTarget;
    if (!target) return;
    setRemoving(true);
    setRemoveError(null);
    try {
      await chatApi.deleteProjectAttachment(projectId, target.id);
    } catch (err) {
      // Already gone: the list was out of date, and now it is right.
      if (!isNotFound(err)) {
        setRemoveError(reason(err));
        setRemoving(false);
        setRemoveTarget(null);
        return;
      }
    }
    setFiles((prev) => prev.filter((f) => f.id !== target.id));
    setRemoving(false);
    setRemoveTarget(null);
  };

  const textState = (file: ChatAttachment) => {
    switch (file.extraction_status) {
      case "completed":
        return { color: "success" as const, label: t("chat.project_file_read") };
      case "failed":
        return { color: "warning" as const, label: t("chat.project_file_unread") };
      case "skipped":
        return { color: "default" as const, label: t("chat.project_file_picture") };
      default:
        return { color: "default" as const, label: t("chat.project_file_pending") };
    }
  };

  const heading = t("chat.project_files");
  const busy = uploads.some((u) => u.failed === null);

  return (
    <Box component="section" aria-labelledby="project-files-title" sx={{ mt: 1 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography id="project-files-title" variant="subtitle2" sx={{ flex: 1 }}>
          {heading}
        </Typography>
        <Button
          size="small"
          startIcon={<AttachFileIcon />}
          onClick={() => inputRef.current?.click()}
          sx={{ textTransform: "none" }}
        >
          {t("chat.project_files_add")}
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          data-testid="project-file-input"
          aria-label={t("chat.project_files_add")}
          onChange={handlePick}
        />
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
        {t("chat.project_files_hint")}
      </Typography>

      {loadError && (
        <Alert
          severity="error"
          sx={{ my: 0.5 }}
          action={
            <Button color="inherit" size="small" onClick={() => void load()}>
              {t("chat.project_files_retry")}
            </Button>
          }
        >
          {t("chat.project_files_load_failed", { reason: loadError })}
        </Alert>
      )}
      {removeError && (
        <Alert severity="error" sx={{ my: 0.5 }} onClose={() => setRemoveError(null)}>
          {t("chat.project_files_remove_failed", { reason: removeError })}
        </Alert>
      )}

      {loading ? (
        <Box role="status" sx={{ display: "flex", alignItems: "center", gap: 1, py: 1 }}>
          <CircularProgress size={16} aria-hidden />
          <Typography variant="body2" color="text.secondary">
            {t("chat.project_files_loading")}
          </Typography>
        </Box>
      ) : (
        !loadError &&
        files.length === 0 &&
        uploads.length === 0 && (
          <Typography variant="body2" color="text.secondary" data-testid="project-files-empty" sx={{ py: 1 }}>
            {t("chat.project_files_empty")}
          </Typography>
        )
      )}

      <List dense disablePadding aria-label={heading} aria-busy={busy}>
        {files.map((file) => {
          const state = textState(file);
          return (
            <ListItem
              key={file.id}
              disableGutters
              secondaryAction={
                <Tooltip title={t("chat.project_files_remove")}>
                  <IconButton
                    edge="end"
                    size="small"
                    aria-label={t("chat.project_files_remove_named", { name: file.filename })}
                    onClick={() => {
                      setRemoveError(null);
                      setRemoveTarget(file);
                    }}
                  >
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              }
            >
              <ListItemText
                primary={file.filename}
                secondary={
                  <Box component="span" sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                    <span>{fmtBytes(file.size_bytes, i18n.language)}</span>
                    <Chip
                      component="span"
                      size="small"
                      variant="outlined"
                      color={state.color}
                      label={state.label}
                      data-testid="project-file-state"
                      sx={{ height: 20, fontSize: "0.7rem" }}
                    />
                  </Box>
                }
                slotProps={{
                  primary: { noWrap: true, fontSize: "0.875rem" },
                  secondary: { component: "span" },
                }}
                sx={{ pr: 4 }}
              />
            </ListItem>
          );
        })}
        {uploads.map((up) => (
          <ListItem
            key={`up-${up.key}`}
            disableGutters
            secondaryAction={
              up.failed !== null ? (
                <Button
                  size="small"
                  onClick={() => setUploads((prev) => prev.filter((u) => u.key !== up.key))}
                  aria-label={t("chat.project_files_dismiss_named", { name: up.name })}
                >
                  {t("chat.project_files_dismiss")}
                </Button>
              ) : undefined
            }
          >
            {up.failed === null ? (
              <Box role="status" sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <CircularProgress size={16} aria-hidden />
                <Typography variant="body2" noWrap>
                  {t("chat.project_files_uploading", { name: up.name })}
                </Typography>
              </Box>
            ) : (
              <Alert severity="error" sx={{ flex: 1, py: 0, mr: 8 }}>
                {t("chat.project_files_upload_failed", { name: up.name, reason: up.failed })}
              </Alert>
            )}
          </ListItem>
        ))}
      </List>

      <ConfirmDialog
        open={removeTarget !== null}
        title={t("chat.project_files_remove_title")}
        message={t("chat.project_files_remove_confirm", { name: removeTarget?.filename ?? "" })}
        confirmLabel={t("chat.project_files_remove")}
        cancelLabel={t("common.cancel", { defaultValue: "Cancel" })}
        loading={removing}
        onConfirm={() => void confirmRemove()}
        onClose={() => setRemoveTarget(null)}
      />
    </Box>
  );
}
