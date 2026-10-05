/**
 * An agent's definition: its instructions and model, what it may use (knowledge,
 * the gateway's tools, the host's tools, local MCP servers), and what the SDKs
 * show. Controlled: the parent owns the value and decides when to save it.
 */
import { useTranslation } from "react-i18next";

import Autocomplete from "@mui/material/Autocomplete";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import type { AgentDefinition, LocalMcpMode } from "~/api/agents";
import { chatApi } from "~/api/chatClient";
import { ChipListField } from "~/components/ChipListField";
import { SectionCard as Section } from "~/components/SectionCard";
import { useAsyncData } from "~/lib/useAsyncData";

import {
  greetingOf,
  piiModeSwitch,
  serverToolsMode,
  suggestionsOf,
  withPiiModeSwitch,
  withServerToolsMode,
  withUi,
  type PiiModeSwitch,
  type ServerToolsMode,
} from "./agentDefinition";
import { HostToolContractEditor } from "./HostToolContractEditor";

const SERVER_TOOLS_MODES: ServerToolsMode[] = ["default", "listed", "none"];
const LOCAL_MCP_MODES: LocalMcpMode[] = ["disable", "allow"];
const PII_MODE_SWITCHES: PiiModeSwitch[] = ["tenant", "allow", "refuse"];

export interface AgentDefinitionEditorProps {
  value: AgentDefinition;
  onChange: (value: AgentDefinition) => void;
  readOnly?: boolean;
}

export function AgentDefinitionEditor({ value, onChange, readOnly = false }: AgentDefinitionEditorProps) {
  const { t } = useTranslation();
  const { data: models } = useAsyncData(
    async () => (await chatApi.listModels()).filter((m) => m.enabled && (m.kind ?? "chat") === "chat").map((m) => m.alias),
    [],
    { initialValue: [] as string[] },
  );
  const set = <K extends keyof AgentDefinition>(key: K, next: AgentDefinition[K]) => onChange({ ...value, [key]: next });
  const toolsMode = serverToolsMode(value);
  const piiSwitch = piiModeSwitch(value);

  return (
    <Stack spacing={2.5}>
      <Section title={t("agents.definition.behaviour")}>
        <TextField
          label={t("agents.definition.instructions")}
          helperText={t("agents.definition.instructions_help")}
          multiline
          minRows={8}
          maxRows={30}
          fullWidth
          value={value.instructions}
          InputProps={{ readOnly, sx: { fontFamily: "monospace", fontSize: 13 } }}
          onChange={(e) => set("instructions", e.target.value)}
        />
        <Autocomplete<string, false, false, true>
          freeSolo
          options={models}
          value={value.model_alias ?? ""}
          disabled={readOnly}
          onInputChange={(_, next) => set("model_alias", next || null)}
          renderInput={(params) => (
            <TextField {...params} size="small" label={t("agents.definition.model")} helperText={t("agents.definition.model_help")} />
          )}
        />
      </Section>

      <Section title={t("agents.definition.knowledge_and_tools")}>
        <ChipListField
          label={t("agents.definition.knowledge")}
          helperText={t("agents.definition.knowledge_help")}
          value={value.knowledge}
          disabled={readOnly}
          onChange={(next) => set("knowledge", next)}
        />
        <Stack spacing={0.5}>
          <Typography variant="subtitle2">{t("agents.definition.server_tools")}</Typography>
          <RadioGroup row value={toolsMode} onChange={(e) => onChange(withServerToolsMode(value, e.target.value as ServerToolsMode))}>
            {SERVER_TOOLS_MODES.map((mode) => (
              <FormControlLabel
                key={mode}
                value={mode}
                disabled={readOnly}
                control={<Radio size="small" />}
                label={t(`agents.definition.server_tools_${mode}`)}
              />
            ))}
          </RadioGroup>
          {toolsMode === "listed" && (
            <ChipListField
              label={t("agents.definition.server_tools_list")}
              helperText={t("agents.definition.server_tools_help")}
              placeholder="knowledge_*"
              value={(value.server_tools ?? []).filter(Boolean)}
              disabled={readOnly}
              onChange={(next) => set("server_tools", next.length ? next : [""])}
            />
          )}
        </Stack>
        <TextField
          select
          size="small"
          label={t("agents.definition.local_mcp")}
          helperText={t(`agents.definition.local_mcp_${value.local_mcp_mode}_help`)}
          value={value.local_mcp_mode}
          disabled={readOnly}
          onChange={(e) => set("local_mcp_mode", e.target.value as LocalMcpMode)}
          sx={{ maxWidth: 360 }}
        >
          {LOCAL_MCP_MODES.map((mode) => (
            <MenuItem key={mode} value={mode}>
              {t(`agents.definition.local_mcp_${mode}`)}
            </MenuItem>
          ))}
        </TextField>
      </Section>

      <Section title={t("agents.definition.privacy")}>
        <TextField
          select
          size="small"
          label={t("agents.definition.pii_mode")}
          helperText={t(`agents.definition.pii_mode_${piiSwitch}_help`)}
          value={piiSwitch}
          disabled={readOnly}
          onChange={(e) => onChange(withPiiModeSwitch(value, e.target.value as PiiModeSwitch))}
          sx={{ maxWidth: 360 }}
        >
          {PII_MODE_SWITCHES.map((mode) => (
            <MenuItem key={mode} value={mode}>
              {t(`agents.definition.pii_mode_${mode}`)}
            </MenuItem>
          ))}
        </TextField>
      </Section>

      <Section title={t("agents.definition.host_tools")} description={t("agents.definition.host_tools_help")}>
        <HostToolContractEditor
          tools={value.host_tool_contract}
          readOnly={readOnly}
          onChange={(tools) => set("host_tool_contract", tools)}
        />
      </Section>

      <Section title={t("agents.definition.appearance")} description={t("agents.definition.appearance_help")}>
        <TextField
          size="small"
          fullWidth
          label={t("agents.definition.greeting")}
          value={greetingOf(value)}
          InputProps={{ readOnly }}
          onChange={(e) => onChange(withUi(value, { greeting: e.target.value }))}
        />
        <ChipListField
          label={t("agents.definition.suggestions")}
          helperText={t("agents.definition.suggestions_help")}
          value={suggestionsOf(value)}
          disabled={readOnly}
          onChange={(next) => onChange(withUi(value, { suggestions: next }))}
        />
      </Section>

      {Object.keys(value.extensions).length > 0 && (
        <Section title={t("agents.definition.extensions")} description={t("agents.definition.extensions_help")}>
          <Typography component="pre" variant="body2" sx={{ fontFamily: "monospace", m: 0, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(value.extensions, null, 2)}
          </Typography>
        </Section>
      )}
    </Stack>
  );
}
