/** An agent's name and description; its address (slug) never changes. */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";

import { agentsApi, type Agent } from "~/api/agents";
import { FormDialog } from "~/components/FormDialog";
import { useAction } from "~/lib/useAction";

export interface EditAgentDialogProps {
  agent: Agent;
  open: boolean;
  onClose: () => void;
  onSaved: (agent: Agent) => void;
}

export function EditAgentDialog({ agent, open, onClose, onSaved }: EditAgentDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(agent.name);
  const [description, setDescription] = useState(agent.description ?? "");
  const save = useAction();

  useEffect(() => {
    if (!open) return;
    setName(agent.name);
    setDescription(agent.description ?? "");
    save.setError(null);
  }, [open, agent, save.setError]);

  const changed = name.trim() !== agent.name || description.trim() !== (agent.description ?? "");

  return (
    <FormDialog
      open={open}
      title={t("agents.edit_title")}
      loading={save.busy}
      submitLabel={t("common.save")}
      cancelLabel={t("common.cancel")}
      submitDisabled={!name.trim() || !changed}
      onClose={onClose}
      onSubmit={() =>
        void save.run(
          () => agentsApi.update(agent.slug, { name: name.trim(), description: description.trim() || null }),
          onSaved,
        )
      }
    >
      <Stack spacing={2} sx={{ mt: 1 }}>
        {save.error && <Alert severity="error">{save.error}</Alert>}
        <TextField label={t("agents.name")} value={name} required autoFocus onChange={(e) => setName(e.target.value)} />
        <TextField
          label={t("agents.description")}
          value={description}
          multiline
          minRows={2}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Stack>
    </FormDialog>
  );
}
