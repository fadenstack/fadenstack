/**
 * A new agent in one step: start from one of Fadenstack's own apps (Chrome,
 * Office) or from pasted JSON as `POST /api/agents` takes it, name it, and --
 * if wanted -- publish it and say who may use it at once. Afterwards the apps
 * that run it report what they bring (the Apps tab), so the tools need not be
 * typed in.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import Collapse from "@mui/material/Collapse";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DataObjectIcon from "@mui/icons-material/DataObject";

import { agentsApi, type Agent, type AgentDefinition, type AgentTemplate } from "~/api/agents";
import { chatApi } from "~/api/chatClient";
import { principalKey, type PrincipalOption } from "~/api/principals";
import { FormDialog } from "~/components/FormDialog";
import { PrincipalList } from "~/components/PrincipalList";
import { PrincipalPicker } from "~/components/PrincipalPicker";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

import { EMPTY_DEFINITION, SLUG_PATTERN, cleanDefinition, parseAgentImport, slugFromName } from "./agentDefinition";

export interface CreateAgentDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (agent: Agent) => void;
}

export function CreateAgentDialog({ open, onClose, onCreated }: CreateAgentDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [definition, setDefinition] = useState<AgentDefinition>(EMPTY_DEFINITION);
  const [importNote, setImportNote] = useState<{ severity: "success" | "error"; text: string } | null>(null);
  const [template, setTemplate] = useState<string | null>(null);
  const [publish, setPublish] = useState(false);
  const [grants, setGrants] = useState<PrincipalOption[]>([]);
  const create = useAction();
  const templates = useAsyncData(() => (open ? agentsApi.templates() : Promise.resolve([])), [open], {
    initialValue: [] as AgentTemplate[],
  });
  // The apps leave the model to the agent: one published without a model cannot answer.
  const models = useAsyncData(
    async () =>
      open ? (await chatApi.listModels()).filter((m) => m.enabled && (m.kind ?? "chat") === "chat").map((m) => m.alias) : [],
    [open],
    { initialValue: [] as string[] },
  );
  const modelMissing = publish && !definition.model_alias;
  const granted = useMemo(() => new Set(grants.map((g) => principalKey(g.type, g.id))), [grants]);

  const slugValid = SLUG_PATTERN.test(slug);

  function reset() {
    setName("");
    setSlug("");
    setSlugEdited(false);
    setDescription("");
    setImportOpen(false);
    setImportText("");
    setDefinition(EMPTY_DEFINITION);
    setImportNote(null);
    setTemplate(null);
    setPublish(false);
    setGrants([]);
    create.setError(null);
  }

  /** A template fills everything in; it is complete, so it is published at once unless unticked. */
  function applyTemplate(chosen: AgentTemplate) {
    const imported = parseAgentImport(JSON.stringify(chosen.definition));
    setTemplate(chosen.id);
    setDefinition(imported.definition);
    setName(imported.name ?? chosen.name);
    setSlug(imported.slug ?? chosen.slug);
    setSlugEdited(true);
    setDescription(imported.description ?? chosen.description ?? "");
    setPublish(true);
  }
  const chosen = templates.data.find((x) => x.id === template);

  function close() {
    reset();
    onClose();
  }

  function changeName(next: string) {
    setName(next);
    if (!slugEdited) setSlug(slugFromName(next));
  }

  function applyImport() {
    try {
      const imported = parseAgentImport(importText);
      setDefinition(imported.definition);
      if (imported.name && !name) changeName(imported.name);
      if (imported.slug) {
        setSlug(imported.slug);
        setSlugEdited(true);
      }
      if (imported.description && !description) setDescription(imported.description);
      setImportNote({
        severity: "success",
        text: t("agents.import.applied", { tools: imported.definition.host_tool_contract.length }),
      });
    } catch (err) {
      setImportNote({ severity: "error", text: t(err instanceof Error ? err.message : "agents.import.not_json") });
    }
  }

  const submit = () =>
    create.run(
      async () => {
        let agent = await agentsApi.create({
          slug,
          name: name.trim(),
          description: description.trim() || null,
          version: cleanDefinition(definition),
        });
        // The agent exists now: a step after this that fails shows on its page (draft, no access), not here.
        try {
          if (publish) agent = await agentsApi.publish(agent.slug, 1);
          if (grants.length > 0) {
            await agentsApi.setGrants(agent.slug, grants.map((g) => ({ principal_type: g.type, principal_id: g.id })));
          }
        } catch {
          // shown on the agent's page
        }
        return agent;
      },
      (agent) => {
        reset();
        onCreated(agent);
      },
    );

  return (
    <FormDialog
      open={open}
      title={t("agents.create_title")}
      loading={create.busy}
      submitLabel={t("common.create")}
      cancelLabel={t("common.cancel")}
      submitDisabled={!name.trim() || !slugValid || modelMissing}
      onSubmit={() => void submit()}
      onClose={close}
      maxWidth="md"
    >
      <Stack spacing={2} sx={{ mt: 1 }}>
        {create.error && <Alert severity="error">{create.error}</Alert>}
        {templates.data.length > 0 && (
          <Stack spacing={1}>
            <Typography variant="subtitle2">{t("agents.create_from")}</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap role="group" aria-label={t("agents.create_from")}>
              {templates.data.map((x) => (
                <Chip
                  key={x.id}
                  label={x.app}
                  color={template === x.id ? "primary" : "default"}
                  variant={template === x.id ? "filled" : "outlined"}
                  onClick={() => applyTemplate(x)}
                  aria-pressed={template === x.id}
                />
              ))}
            </Stack>
            <Typography variant="caption" color="text.secondary">
              {chosen ? t("agents.import.applied", { tools: chosen.tools }) : t("agents.create_from_hint")}
            </Typography>
          </Stack>
        )}
        <TextField label={t("agents.name")} value={name} required autoFocus onChange={(e) => changeName(e.target.value)} />
        <TextField
          label={t("agents.slug")}
          value={slug}
          required
          error={slug !== "" && !slugValid}
          helperText={slug !== "" && !slugValid ? t("agents.slug_invalid") : t("agents.slug_help")}
          inputProps={{ style: { fontFamily: "monospace" } }}
          onChange={(e) => {
            setSlug(e.target.value);
            setSlugEdited(true);
          }}
        />
        <TextField
          label={t("agents.description")}
          value={description}
          multiline
          minRows={2}
          onChange={(e) => setDescription(e.target.value)}
        />
        <TextField
          select
          label={t("agents.definition.model")}
          value={definition.model_alias ?? ""}
          required={publish}
          error={modelMissing && Boolean(template)}
          helperText={t("agents.create_model_help")}
          onChange={(e) => setDefinition((d) => ({ ...d, model_alias: e.target.value || null }))}
        >
          <MenuItem value="">
            <em>{t("agents.create_model_none")}</em>
          </MenuItem>
          {models.data.map((m) => (
            <MenuItem key={m} value={m}>
              {m}
            </MenuItem>
          ))}
        </TextField>
        <Stack spacing={1}>
          <Typography variant="subtitle2">{t("agents.create_grants")}</Typography>
          <PrincipalPicker
            search={agentsApi.findPrincipals}
            exclude={granted}
            placeholder={t("agents.access.add_users")}
            onPick={(p) => setGrants((prev) => [...prev, p])}
          />
          {grants.length > 0 && (
            <PrincipalList items={grants} emptyText="" onRemove={(p) => setGrants((prev) => prev.filter((g) => g !== p))} />
          )}
        </Stack>
        <FormControlLabel
          control={<Checkbox checked={publish} onChange={(e) => setPublish(e.target.checked)} />}
          label={t("agents.create_publish")}
        />
        <Typography variant="body2" color="text.secondary">
          {publish ? t("agents.create_publish_hint") : t("agents.create_hint")}
        </Typography>
        <Button
          size="small"
          startIcon={<DataObjectIcon />}
          sx={{ alignSelf: "flex-start" }}
          onClick={() => setImportOpen((v) => !v)}
        >
          {t("agents.import.toggle")}
        </Button>
        <Collapse in={importOpen} unmountOnExit>
          <Stack spacing={1}>
            <TextField
              multiline
              minRows={6}
              maxRows={16}
              placeholder={t("agents.import.placeholder")}
              value={importText}
              InputProps={{ sx: { fontFamily: "monospace", fontSize: 13 } }}
              onChange={(e) => setImportText(e.target.value)}
            />
            <Button size="small" variant="outlined" sx={{ alignSelf: "flex-start" }} disabled={!importText.trim()} onClick={applyImport}>
              {t("agents.import.apply")}
            </Button>
            {importNote && <Alert severity={importNote.severity}>{importNote.text}</Alert>}
          </Stack>
        </Collapse>
      </Stack>
    </FormDialog>
  );
}
