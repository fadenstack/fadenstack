/**
 * Editing an agent: the latest version's definition, saved as a new version
 * (versions never change), and published when it is ready.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import PublishIcon from "@mui/icons-material/Publish";
import SaveIcon from "@mui/icons-material/Save";
import UndoIcon from "@mui/icons-material/Undo";

import { agentsApi, type Agent, type AgentDefinition, type AgentVersion } from "~/api/agents";
import { useAction } from "~/lib/useAction";

import { AgentDefinitionEditor } from "./AgentDefinitionEditor";
import { changedFields, cleanDefinition, definitionOf, definitionProblems, hasUnpublishedVersion } from "./agentDefinition";

export interface AgentDefinitionPanelProps {
  agent: Agent;
  /** The version being edited: the latest one. A new latest version starts a new draft. */
  latest: AgentVersion | null;
  /** After a new version or a publish: reload the agent and its versions. */
  onSaved: () => void;
}

export function AgentDefinitionPanel({ agent, latest, onSaved }: AgentDefinitionPanelProps) {
  const { t } = useTranslation();
  const baseline = definitionOf(latest);
  const [draft, setDraft] = useState<AgentDefinition>(baseline);
  const [draftOf, setDraftOf] = useState(latest?.id);
  if (draftOf !== latest?.id) {
    // Saved here or elsewhere: the new version is what is edited from now on.
    // A refresh that brings the same version keeps the draft.
    setDraftOf(latest?.id);
    setDraft(baseline);
  }
  const save = useAction();

  const changed = changedFields(baseline, draft);
  const problems = definitionProblems(draft);
  const canSave = agent.can_manage && changed.length > 0 && problems.length === 0 && !save.busy;
  const canPublish = agent.can_manage && agent.status !== "retired";

  async function saveVersion(publish: boolean) {
    let saved = false;
    await save.run(async () => {
      const version = await agentsApi.addVersion(agent.slug, cleanDefinition(draft));
      saved = true;
      if (publish) await agentsApi.publish(agent.slug, version.version);
    });
    // Saved but not published: still a new version, and the error says why.
    if (saved) onSaved();
  }

  const publishLatest = () => save.run(() => agentsApi.publish(agent.slug, agent.latest_version!), onSaved);

  return (
    <Stack spacing={2}>
      {!agent.can_manage && <Alert severity="info">{t("agents.definition.read_only")}</Alert>}
      {save.error && (
        <Alert severity="error" onClose={() => save.setError(null)}>
          {save.error}
        </Alert>
      )}
      {problems.map((key) => (
        <Alert key={key} severity="warning">
          {t(key)}
        </Alert>
      ))}

      {agent.can_manage && (
        <Paper
          variant="outlined"
          sx={{ p: 1.5, position: "sticky", top: 8, zIndex: 2, bgcolor: "background.paper" }}
        >
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="body2" color="text.secondary">
                {changed.length > 0
                  ? t("agents.definition.unsaved", {
                      fields: changed.map((f) => t(`agents.field.${f}`)).join(", "),
                    })
                  : latest
                    ? t("agents.definition.editing_version", { version: latest.version })
                    : t("agents.definition.no_version")}
              </Typography>
            </Box>
            {changed.length > 0 ? (
              <>
                <Button size="small" startIcon={<UndoIcon />} disabled={save.busy} onClick={() => setDraft(baseline)}>
                  {t("agents.definition.discard")}
                </Button>
                <Button size="small" variant="outlined" startIcon={<SaveIcon />} disabled={!canSave} onClick={() => void saveVersion(false)}>
                  {t("agents.definition.save_version")}
                </Button>
                {canPublish && (
                  <Button size="small" variant="contained" startIcon={<PublishIcon />} disabled={!canSave} onClick={() => void saveVersion(true)}>
                    {t("agents.definition.save_and_publish")}
                  </Button>
                )}
              </>
            ) : (
              canPublish &&
              hasUnpublishedVersion(agent) && (
                <Button size="small" variant="contained" startIcon={<PublishIcon />} disabled={save.busy} onClick={() => void publishLatest()}>
                  {t("agents.publish_version", { version: agent.latest_version })}
                </Button>
              )
            )}
          </Stack>
        </Paper>
      )}

      <AgentDefinitionEditor value={draft} onChange={setDraft} readOnly={!agent.can_manage} />
    </Stack>
  );
}
