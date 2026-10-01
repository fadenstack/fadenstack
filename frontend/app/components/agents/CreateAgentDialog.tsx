/**
 * A new agent: its name and address, and optionally a definition to start from
 * -- pasted JSON as `POST /api/agents` takes it (e.g. the Office agents'
 * contracts/*.json), a version's fields, or a host tool contract.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DataObjectIcon from "@mui/icons-material/DataObject";

import { agentsApi, type Agent, type AgentDefinition } from "~/api/agents";
import { FormDialog } from "~/components/FormDialog";
import { useAction } from "~/lib/useAction";

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
  const create = useAction();

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
    create.setError(null);
  }

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
      () =>
        agentsApi.create({
          slug,
          name: name.trim(),
          description: description.trim() || null,
          version: cleanDefinition(definition),
        }),
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
      submitDisabled={!name.trim() || !slugValid}
      onSubmit={() => void submit()}
      onClose={close}
      maxWidth="md"
    >
      <Stack spacing={2} sx={{ mt: 1 }}>
        {create.error && <Alert severity="error">{create.error}</Alert>}
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
        <Typography variant="body2" color="text.secondary">
          {t("agents.create_hint")}
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
