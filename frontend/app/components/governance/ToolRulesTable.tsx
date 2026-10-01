/** Tool rules: deny a tool, ask before it runs, or allow it, by name; the lowest priority that matches decides. */
import { useTranslation } from "react-i18next";

import Chip, { type ChipProps } from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { governanceApi, type ToolRule, type ToolRuleAction, type ToolRuleFields } from "~/api/governance";
import type { ColumnDef } from "~/components/DataTable";

import { PatternRulesTable, type RuleFormProps } from "./PatternRulesTable";

const ACTIONS: ToolRuleAction[] = ["deny", "approval_required", "allow"];
const ACTION_COLOR: Record<ToolRuleAction, ChipProps["color"]> = {
  deny: "error",
  approval_required: "warning",
  allow: "success",
};

const BLANK: ToolRuleFields = {
  name: "",
  tool_pattern: "",
  action: "deny",
  message: "",
  category: "tool_rule",
  priority: 100,
  enabled: true,
};

const fieldsOf = ({ name, tool_pattern, action, message, category, priority, enabled }: ToolRule): ToolRuleFields => ({
  name,
  tool_pattern,
  action,
  message,
  category,
  priority,
  enabled,
});

const valid = (f: ToolRuleFields) => Boolean(f.name.trim() && f.tool_pattern.trim() && f.message.trim());

export function ToolRuleActionChip({ action }: { action: ToolRuleAction }) {
  const { t } = useTranslation();
  return <Chip size="small" color={ACTION_COLOR[action]} label={t(`governance.rules.action.${action}`)} />;
}

function ToolRuleForm({ value, onChange }: RuleFormProps<ToolRuleFields>) {
  const { t } = useTranslation();
  const set = <K extends keyof ToolRuleFields>(key: K, v: ToolRuleFields[K]) => onChange({ ...value, [key]: v });
  return (
    <>
      <TextField label={t("governance.rules.name")} value={value.name} required autoFocus onChange={(e) => set("name", e.target.value)} />
      <TextField
        label={t("governance.pattern")}
        value={value.tool_pattern}
        required
        helperText={t("governance.pattern_help")}
        slotProps={{ htmlInput: { style: { fontFamily: "monospace" } } }}
        onChange={(e) => set("tool_pattern", e.target.value)}
      />
      <TextField select label={t("governance.rules.action_label")} value={value.action} onChange={(e) => set("action", e.target.value as ToolRuleAction)}>
        {ACTIONS.map((a) => (
          <MenuItem key={a} value={a}>
            {t(`governance.rules.action.${a}`)}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        label={t("governance.rules.message")}
        value={value.message}
        required
        multiline
        minRows={2}
        helperText={t("governance.rules.message_help")}
        onChange={(e) => set("message", e.target.value)}
      />
      <TextField label={t("governance.rules.category")} value={value.category} onChange={(e) => set("category", e.target.value)} />
      <TextField
        type="number"
        label={t("governance.priority")}
        value={value.priority}
        helperText={t("governance.priority_help")}
        slotProps={{ htmlInput: { min: 0, max: 100000 } }}
        onChange={(e) => set("priority", Number(e.target.value))}
      />
      <FormControlLabel
        control={<Switch checked={value.enabled} onChange={(e) => set("enabled", e.target.checked)} />}
        label={t("governance.rules.enabled")}
      />
    </>
  );
}

export function ToolRulesTable() {
  const { t } = useTranslation();
  const columns: ColumnDef<ToolRule>[] = [
    {
      key: "action",
      label: t("governance.rules.action_label"),
      sortable: true,
      sortValue: (r) => ACTIONS.indexOf(r.action),
      render: (r) => <ToolRuleActionChip action={r.action} />,
      minWidth: 130,
    },
    {
      key: "name",
      label: t("governance.rules.name"),
      searchValue: (r) => `${r.name} ${r.message}`,
      render: (r) => (
        <>
          <Typography variant="body2" fontWeight={600} sx={{ opacity: r.enabled ? 1 : 0.5 }}>
            {r.name}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {r.message}
          </Typography>
        </>
      ),
      minWidth: 240,
    },
    { key: "category", label: t("governance.rules.category"), render: (r) => r.category, minWidth: 110 },
    {
      key: "priority",
      label: t("governance.priority"),
      sortable: true,
      sortValue: (r) => r.priority,
      render: (r) => r.priority,
      align: "right",
    },
    {
      key: "enabled",
      label: t("governance.rules.enabled"),
      render: (r) =>
        r.enabled ? t("common.yes") : <Chip size="small" variant="outlined" label={t("governance.rules.off")} />,
    },
  ];

  return (
    <PatternRulesTable
      api={governanceApi.toolRules}
      columns={columns}
      blank={BLANK}
      fieldsOf={fieldsOf}
      Form={ToolRuleForm}
      valid={valid}
      text={{
        title: t("governance.rules.title"),
        description: t("governance.rules.description"),
        add: t("governance.rules.add"),
        createTitle: t("governance.rules.create_title"),
        editTitle: t("governance.rules.edit_title"),
        empty: t("governance.rules.empty"),
        removeTitle: t("governance.rules.remove_title"),
      }}
    />
  );
}
