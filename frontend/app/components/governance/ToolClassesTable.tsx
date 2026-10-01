/**
 * Tool classes: what tools do (read, write, destructive), by name, for the
 * tool modes to act on -- or trust what their MCP server says (annotations).
 */
import { useTranslation } from "react-i18next";

import Chip from "@mui/material/Chip";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

import { governanceApi, type ToolClassFields, type ToolClassRule, type ToolClassValue } from "~/api/governance";
import type { ColumnDef } from "~/components/DataTable";
import { ToolClassChip } from "~/components/ToolClassChip";

import { PatternRulesTable, type RuleFormProps } from "./PatternRulesTable";

const CLASSES: ToolClassValue[] = ["read", "write", "destructive", "annotations"];

/** `open_world` as a select: not said, yes, no. */
type OpenWorld = "unset" | "yes" | "no";
const openWorldOf = (v: boolean | null): OpenWorld => (v === null ? "unset" : v ? "yes" : "no");
const openWorldValue = (v: OpenWorld): boolean | null => (v === "unset" ? null : v === "yes");

const BLANK: ToolClassFields = { tool_pattern: "", tool_class: "read", open_world: null, note: null, priority: 100 };

const fieldsOf = ({ tool_pattern, tool_class, open_world, note, priority }: ToolClassRule): ToolClassFields => ({
  tool_pattern,
  tool_class,
  open_world,
  note,
  priority,
});

const valid = (f: ToolClassFields) => Boolean(f.tool_pattern.trim());

function ClassChip({ value }: { value: ToolClassValue }) {
  const { t } = useTranslation();
  return value === "annotations" ? (
    <Chip size="small" variant="outlined" label={t("governance.classes.annotations")} />
  ) : (
    <ToolClassChip value={value} />
  );
}

function ToolClassForm({ value, onChange }: RuleFormProps<ToolClassFields>) {
  const { t } = useTranslation();
  const set = <K extends keyof ToolClassFields>(key: K, v: ToolClassFields[K]) => onChange({ ...value, [key]: v });
  return (
    <>
      <TextField
        label={t("governance.pattern")}
        value={value.tool_pattern}
        required
        autoFocus
        helperText={t("governance.pattern_help")}
        slotProps={{ htmlInput: { style: { fontFamily: "monospace" } } }}
        onChange={(e) => set("tool_pattern", e.target.value)}
      />
      <TextField
        select
        label={t("governance.classes.class")}
        value={value.tool_class}
        helperText={t(`governance.classes.help_${value.tool_class}`)}
        onChange={(e) => set("tool_class", e.target.value as ToolClassValue)}
      >
        {CLASSES.map((c) => (
          <MenuItem key={c} value={c}>
            {c === "annotations" ? t("governance.classes.annotations") : t(`tools.class.${c}`)}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        select
        label={t("governance.classes.open_world")}
        value={openWorldOf(value.open_world)}
        helperText={t("governance.classes.open_world_help")}
        onChange={(e) => set("open_world", openWorldValue(e.target.value as OpenWorld))}
      >
        <MenuItem value="unset">{t("governance.classes.open_world_unset")}</MenuItem>
        <MenuItem value="yes">{t("common.yes")}</MenuItem>
        <MenuItem value="no">{t("common.no")}</MenuItem>
      </TextField>
      <TextField
        label={t("governance.classes.note")}
        value={value.note ?? ""}
        multiline
        onChange={(e) => set("note", e.target.value || null)}
      />
      <TextField
        type="number"
        label={t("governance.priority")}
        value={value.priority}
        helperText={t("governance.priority_help")}
        slotProps={{ htmlInput: { min: 0, max: 100000 } }}
        onChange={(e) => set("priority", Number(e.target.value))}
      />
    </>
  );
}

export function ToolClassesTable() {
  const { t } = useTranslation();
  const columns: ColumnDef<ToolClassRule>[] = [
    {
      key: "class",
      label: t("governance.classes.class"),
      sortable: true,
      sortValue: (r) => CLASSES.indexOf(r.tool_class),
      render: (r) => <ClassChip value={r.tool_class} />,
      minWidth: 130,
    },
    {
      key: "open_world",
      label: t("governance.classes.open_world"),
      render: (r) => (r.open_world === null ? "—" : r.open_world ? t("common.yes") : t("common.no")),
    },
    {
      key: "note",
      label: t("governance.classes.note"),
      searchValue: (r) => r.note ?? "",
      render: (r) => r.note ?? "",
      minWidth: 200,
    },
    {
      key: "priority",
      label: t("governance.priority"),
      sortable: true,
      sortValue: (r) => r.priority,
      render: (r) => r.priority,
      align: "right",
    },
  ];

  return (
    <PatternRulesTable
      api={governanceApi.toolClasses}
      columns={columns}
      blank={BLANK}
      fieldsOf={fieldsOf}
      Form={ToolClassForm}
      valid={valid}
      text={{
        title: t("governance.classes.title"),
        description: t("governance.classes.description"),
        add: t("governance.classes.add"),
        createTitle: t("governance.classes.create_title"),
        editTitle: t("governance.classes.edit_title"),
        empty: t("governance.classes.empty"),
        removeTitle: t("governance.classes.remove_title"),
      }}
    />
  );
}
