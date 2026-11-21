/**
 * ProjectSettingsDialog — edit a project's name, description, system prompt,
 * default model and files.
 */
import { useEffect, useState } from "react";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

import { chatApi } from "~/api/chatClient";
import { isChatModel, type ChatProject } from "~/api/chatTypes";
import ProjectFiles from "./ProjectFiles";

interface Props {
  open: boolean;
  project: ChatProject | null;
  onClose: () => void;
  onSave: (
    id: string,
    updates: {
      name?: string;
      description?: string;
      system_instructions?: string;
      /** ``null`` takes the project's default model away: the chat's own choice. */
      model_alias?: string | null;
    },
  ) => Promise<void>;
}

export default function ProjectSettingsDialog({
  open,
  project,
  onClose,
  onSave,
}: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  // "" is None: the chat's own choice.
  const [modelAlias, setModelAlias] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (project) {
      setName(project.name);
      setDescription(project.description ?? "");
      setSystemPrompt(project.system_instructions ?? "");
      setModelAlias(project.model_alias ?? "");
    }
  }, [project]);

  // The chat's own model list, chat models only (an embedding model fails on the first message).
  useEffect(() => {
    if (!open) return;
    let live = true;
    chatApi
      .listModels()
      .then((all) => {
        if (live) setModels(all.filter(isChatModel).map((m) => m.alias));
      })
      .catch(() => {
        // The picker then offers None, and the project's current model if it has one.
      });
    return () => {
      live = false;
    };
  }, [open]);

  const handleSave = async () => {
    if (!project) return;
    setSaving(true);
    try {
      await onSave(project.id, {
        name: name.trim(),
        description: description.trim() || undefined,
        system_instructions: systemPrompt.trim() || undefined,
        // Sent only when it changed.
        ...(modelAlias !== (project.model_alias ?? "") ? { model_alias: modelAlias || null } : {}),
      });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {t("chat.project_settings", { defaultValue: "Project Settings" })}
      </DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}
      >
        <TextField
          label={t("chat.project_name", { defaultValue: "Project name" })}
          value={name}
          onChange={(e) => setName(e.target.value)}
          size="small"
          fullWidth
        />
        <TextField
          label={t("chat.project_description", { defaultValue: "Description" })}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          size="small"
          fullWidth
        />
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
          {t("chat.system_prompt_hint", {
            defaultValue:
              "System prompt is prepended to every message in this project. Use it to define the project context, persona, or constraints.",
          })}
        </Typography>
        <TextField
          label={t("chat.system_prompt", { defaultValue: "System prompt" })}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          multiline
          minRows={4}
          maxRows={12}
          fullWidth
          placeholder={t("chat.system_prompt_placeholder", {
            defaultValue: "You are a helpful assistant that specialises in…",
          })}
        />
        <TextField
          select
          label={t("chat.project_model")}
          value={modelAlias}
          onChange={(e) => setModelAlias(e.target.value)}
          size="small"
          fullWidth
          helperText={t("chat.project_model_hint")}
          slotProps={{ select: { displayEmpty: true } }}
        >
          <MenuItem value="">{t("chat.project_model_none")}</MenuItem>
          {/* The project's model stays in the list even if the list does not carry it now. */}
          {(modelAlias && !models.includes(modelAlias) ? [modelAlias, ...models] : models).map((alias) => (
            <MenuItem key={alias} value={alias}>
              {alias}
            </MenuItem>
          ))}
        </TextField>
        {project && <ProjectFiles projectId={project.id} />}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          {t("common.cancel", { defaultValue: "Cancel" })}
        </Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={saving || !name.trim()}
        >
          {t("common.save", { defaultValue: "Save" })}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
