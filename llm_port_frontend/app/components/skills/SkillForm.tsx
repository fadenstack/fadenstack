/**
 * The fields of a skill, shared by the admin pages, the user's skill pages
 * and the chat's "create skill" dialog.
 *
 * Two kinds of fields: the skill's content (description, instructions, and
 * llm.port's tool lists and triggers) is saved as a new version; its catalog
 * fields (title, tags, activation, priority) are changed on the skill itself.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  SkillActivation,
  SkillDetail,
  SkillManifest,
  UpdateSkillPayload,
} from "~/api/skills";

// ── Values ─────────────────────────────────────────────────────────────────

export interface SkillFormValues {
  name: string;
  description: string;
  body: string;
  tags: string;
  activation: SkillActivation;
  priority: number;
  keywords: string;
  fileTypes: string;
  allowedTools: string;
  preferredTools: string;
  forbiddenTools: string;
  knowledgeSources: string;
  changeNote: string;
}

export const EMPTY_SKILL_FORM: SkillFormValues = {
  name: "",
  description: "",
  body: "",
  tags: "",
  activation: "auto",
  priority: 50,
  keywords: "",
  fileTypes: "",
  allowedTools: "",
  preferredTools: "",
  forbiddenTools: "",
  knowledgeSources: "",
  changeNote: "",
};

export function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

function joinCsv(values: string[] | undefined): string {
  return (values ?? []).join(", ");
}

export function valuesFromSkill(skill: SkillDetail): SkillFormValues {
  const m = skill.manifest ?? {};
  return {
    name: skill.name,
    description: skill.description,
    body: skill.body_markdown,
    tags: joinCsv(skill.tags),
    activation: skill.activation,
    priority: skill.priority,
    keywords: joinCsv(m.trigger_rules?.keywords),
    fileTypes: joinCsv(m.trigger_rules?.file_types),
    allowedTools: joinCsv(m.allowed_tools),
    preferredTools: joinCsv(m.preferred_tools),
    forbiddenTools: joinCsv(m.forbidden_tools),
    knowledgeSources: joinCsv(m.knowledge_sources),
    changeNote: "",
  };
}

export function manifestFromValues(values: SkillFormValues): SkillManifest {
  const manifest: SkillManifest = {};
  const allowed = splitCsv(values.allowedTools);
  const preferred = splitCsv(values.preferredTools);
  const forbidden = splitCsv(values.forbiddenTools);
  const knowledge = splitCsv(values.knowledgeSources);
  const keywords = splitCsv(values.keywords);
  const fileTypes = splitCsv(values.fileTypes);
  if (allowed.length) manifest.allowed_tools = allowed;
  if (preferred.length) manifest.preferred_tools = preferred;
  if (forbidden.length) manifest.forbidden_tools = forbidden;
  if (knowledge.length) manifest.knowledge_sources = knowledge;
  if (keywords.length || fileTypes.length) {
    manifest.trigger_rules = {};
    if (keywords.length) manifest.trigger_rules.keywords = keywords;
    if (fileTypes.length) manifest.trigger_rules.file_types = fileTypes;
  }
  return manifest;
}

/** Whether the values hold content the latest version does not. */
export function contentChanged(values: SkillFormValues, skill: SkillDetail): boolean {
  const saved = valuesFromSkill(skill);
  return (
    values.description !== saved.description ||
    values.body !== saved.body ||
    JSON.stringify(manifestFromValues(values)) !== JSON.stringify(manifestFromValues(saved))
  );
}

/** The catalog fields that differ from the skill's. */
export function catalogChanges(values: SkillFormValues, skill: SkillDetail): UpdateSkillPayload {
  const changes: UpdateSkillPayload = {};
  if (values.name.trim() !== skill.name) changes.name = values.name.trim();
  if (JSON.stringify(splitCsv(values.tags)) !== JSON.stringify(skill.tags ?? [])) {
    changes.tags = splitCsv(values.tags);
  }
  if (values.activation !== skill.activation) changes.activation = values.activation;
  if (values.priority !== skill.priority) changes.priority = values.priority;
  return changes;
}

type OnChange = <K extends keyof SkillFormValues>(key: K, value: SkillFormValues[K]) => void;

// ── Instructions ───────────────────────────────────────────────────────────

export function SkillInstructionsFields({
  values,
  onChange,
  readOnly,
  showChangeNote,
  minRows = 14,
}: {
  values: SkillFormValues;
  onChange: OnChange;
  readOnly?: boolean;
  showChangeNote?: boolean;
  minRows?: number;
}) {
  const { t } = useTranslation();
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("md"));
  const [pane, setPane] = useState(0);

  const editor = (
    <TextField
      multiline
      fullWidth
      minRows={minRows}
      value={values.body}
      onChange={(e) => onChange("body", e.target.value)}
      disabled={readOnly}
      placeholder={t("skills.body_placeholder")}
      slotProps={{ htmlInput: { spellCheck: false } }}
      sx={{
        "& .MuiInputBase-root": { fontFamily: "monospace", fontSize: 14, alignItems: "flex-start" },
      }}
    />
  );
  const preview = (
    <Paper variant="outlined" sx={{ px: 2, py: 1, minHeight: minRows * 20, overflow: "auto" }}>
      {values.body.trim() ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{values.body}</ReactMarkdown>
      ) : (
        <Typography color="text.secondary" variant="body2" sx={{ py: 1 }}>
          {t("skills.preview_empty")}
        </Typography>
      )}
    </Paper>
  );

  return (
    <Stack spacing={2}>
      <TextField
        label={t("skills.description")}
        value={values.description}
        onChange={(e) => onChange("description", e.target.value)}
        disabled={readOnly}
        fullWidth
        multiline
        minRows={2}
        helperText={t("skills.description_hint")}
        slotProps={{ htmlInput: { maxLength: 1024 } }}
      />
      {readOnly ? (
        preview
      ) : narrow ? (
        <Box>
          <Tabs value={pane} onChange={(_, v) => setPane(v)} sx={{ minHeight: 36 }}>
            <Tab label={t("skills.markdown_editor")} sx={{ minHeight: 36 }} />
            <Tab label={t("skills.preview")} sx={{ minHeight: 36 }} />
          </Tabs>
          <Box sx={{ pt: 1 }}>{pane === 0 ? editor : preview}</Box>
        </Box>
      ) : (
        <Stack direction="row" spacing={2} alignItems="stretch">
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              {t("skills.markdown_editor")}
            </Typography>
            {editor}
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              {t("skills.preview")}
            </Typography>
            {preview}
          </Box>
        </Stack>
      )}
      {showChangeNote && !readOnly && (
        <TextField
          label={t("skills.change_note")}
          value={values.changeNote}
          onChange={(e) => onChange("changeNote", e.target.value)}
          fullWidth
          size="small"
          placeholder={t("skills.change_note_placeholder")}
          slotProps={{ htmlInput: { maxLength: 500 } }}
        />
      )}
    </Stack>
  );
}

// ── Title, when it comes in, tools ─────────────────────────────────────────

const ACTIVATIONS: SkillActivation[] = ["manual", "auto", "always"];

export function SkillActivationField({
  value,
  onChange,
  disabled,
  allowAlways = true,
}: {
  value: SkillActivation;
  onChange: (value: SkillActivation) => void;
  disabled?: boolean;
  allowAlways?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <TextField
      select
      label={t("skills.activation")}
      value={value}
      onChange={(e) => onChange(e.target.value as SkillActivation)}
      disabled={disabled}
      fullWidth
      helperText={t(`skills.activation_${value}_hint`)}
    >
      {ACTIVATIONS.filter((a) => allowAlways || a !== "always" || a === value).map((a) => (
        <MenuItem key={a} value={a}>
          {t(`skills.activation_${a}`)}
        </MenuItem>
      ))}
    </TextField>
  );
}

export function SkillSettingsFields({
  values,
  onChange,
  readOnly,
  canChangeActivation,
  allowAlways,
}: {
  values: SkillFormValues;
  onChange: OnChange;
  readOnly?: boolean;
  /** Activation and priority are the owner's (or an administrator's). */
  canChangeActivation?: boolean;
  /** An always-on skill the organization sees is for administrators. */
  allowAlways?: boolean;
}) {
  const { t } = useTranslation();
  const ownerOnly = readOnly || !canChangeActivation;
  return (
    <Stack spacing={2} sx={{ maxWidth: 720 }}>
      <TextField
        label={t("skills.skill_name")}
        value={values.name}
        onChange={(e) => onChange("name", e.target.value)}
        disabled={readOnly}
        fullWidth
        required
      />
      <TextField
        label={t("skills.tags")}
        value={values.tags}
        onChange={(e) => onChange("tags", e.target.value)}
        disabled={readOnly}
        fullWidth
        helperText={t("skills.csv_hint")}
      />
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <SkillActivationField
          value={values.activation}
          onChange={(v) => onChange("activation", v)}
          disabled={ownerOnly}
          allowAlways={allowAlways}
        />
        <TextField
          label={t("skills.priority")}
          type="number"
          value={values.priority}
          onChange={(e) =>
            onChange("priority", Math.max(0, Math.min(100, Number(e.target.value) || 0)))
          }
          disabled={ownerOnly}
          sx={{ minWidth: 160 }}
          helperText={t("skills.priority_hint")}
          slotProps={{ htmlInput: { min: 0, max: 100 } }}
        />
      </Stack>

      <Typography variant="subtitle2" sx={{ pt: 1 }}>
        {t("skills.triggers_title")}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: "0 !important" }}>
        {t("skills.triggers_hint")}
      </Typography>
      <TextField
        label={t("skills.trigger_keywords")}
        value={values.keywords}
        onChange={(e) => onChange("keywords", e.target.value)}
        disabled={readOnly}
        fullWidth
        helperText={t("skills.csv_hint")}
      />
      {/* File-type triggers are kept (import, export, the API) but not
          offered: the gateway does not yet tell the resolver which files a
          chat carries, so they would never fire. */}

      <Typography variant="subtitle2" sx={{ pt: 1 }}>
        {t("skills.tools_title")}
      </Typography>
      <TextField
        label={t("skills.tools_allowed")}
        value={values.allowedTools}
        onChange={(e) => onChange("allowedTools", e.target.value)}
        disabled={readOnly}
        fullWidth
        helperText={t("skills.tools_hint")}
      />
      <TextField
        label={t("skills.tools_preferred")}
        value={values.preferredTools}
        onChange={(e) => onChange("preferredTools", e.target.value)}
        disabled={readOnly}
        fullWidth
        helperText={t("skills.csv_hint")}
      />
      <TextField
        label={t("skills.tools_forbidden")}
        value={values.forbiddenTools}
        onChange={(e) => onChange("forbiddenTools", e.target.value)}
        disabled={readOnly}
        fullWidth
        helperText={t("skills.csv_hint")}
      />
      <TextField
        label={t("skills.knowledge_sources")}
        value={values.knowledgeSources}
        onChange={(e) => onChange("knowledgeSources", e.target.value)}
        disabled={readOnly}
        fullWidth
        helperText={t("skills.csv_hint")}
      />
    </Stack>
  );
}
