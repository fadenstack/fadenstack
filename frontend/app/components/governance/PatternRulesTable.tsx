/**
 * An ordered list of rules over tool names -- tool rules, tool classes --
 * with add, change and remove. The kind of rule brings its columns and its
 * form; listing, the dialog, removing and errors are the same for every kind.
 */
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";

import type { RuleApi, Stored } from "~/api/governance";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { FormDialog } from "~/components/FormDialog";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

/** What every kind of rule has: where it is stored, and the tool names it covers. */
type Rule = Stored & { tool_pattern: string };

export interface RuleFormProps<Fields> {
  value: Fields;
  onChange: (value: Fields) => void;
}

export interface PatternRulesTableProps<Fields, R extends Rule & Fields> {
  api: RuleApi<Fields, R>;
  /** The kind's own columns; the pattern and the actions are added. */
  columns: ColumnDef<R>[];
  /** A new rule's fields. */
  blank: Fields;
  /** A rule's fields, for the form. */
  fieldsOf: (rule: R) => Fields;
  Form: (props: RuleFormProps<Fields>) => ReactNode;
  /** Whether the form may be saved. */
  valid: (fields: Fields) => boolean;
  text: {
    title: string;
    description: string;
    add: string;
    createTitle: string;
    editTitle: string;
    empty: string;
    removeTitle: string;
  };
}

export function PatternRulesTable<Fields, R extends Rule & Fields>({
  api,
  columns,
  blank,
  fieldsOf,
  Form,
  valid,
  text,
}: PatternRulesTableProps<Fields, R>) {
  const { t } = useTranslation();
  const rules = useAsyncData(api.list, [api], { initialValue: [] as R[] });
  const [editing, setEditing] = useState<{ id: string | null; fields: Fields } | null>(null);
  const [removing, setRemoving] = useState<R | null>(null);
  const save = useAction();
  const remove = useAction();

  const reload = () => rules.refresh(true);

  const allColumns: ColumnDef<R>[] = [
    {
      key: "tool_pattern",
      label: t("governance.pattern"),
      sortable: true,
      sortValue: (r) => r.tool_pattern,
      searchValue: (r) => r.tool_pattern,
      render: (r) => <code>{r.tool_pattern}</code>,
      minWidth: 180,
    },
    ...columns,
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      hideable: false,
      render: (r) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          <Tooltip title={t("common.edit")}>
            <IconButton size="small" onClick={() => setEditing({ id: r.id, fields: fieldsOf(r) })}>
              <EditIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("common.delete")}>
            <IconButton size="small" color="error" onClick={() => setRemoving(r)}>
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ];

  const closeEditor = () => {
    setEditing(null);
    save.setError(null);
  };

  return (
    <Stack spacing={1.5}>
      <Alert severity="info" variant="outlined">
        {text.description}
      </Alert>
      {remove.error && (
        <Alert severity="error" onClose={() => remove.setError(null)}>
          {remove.error}
        </Alert>
      )}
      <DataTable
        title={text.title}
        rows={rules.data}
        columns={allColumns}
        rowKey={(r) => r.id}
        loading={rules.loading}
        error={rules.error}
        onRefresh={() => void rules.refresh()}
        emptyMessage={text.empty}
        toolbarActions={
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setEditing({ id: null, fields: blank })}>
            {text.add}
          </Button>
        }
      />

      <FormDialog
        open={editing !== null}
        title={editing?.id ? text.editTitle : text.createTitle}
        loading={save.busy}
        submitLabel={t("common.save")}
        cancelLabel={t("common.cancel")}
        submitDisabled={!editing || !valid(editing.fields)}
        onClose={closeEditor}
        onSubmit={() => {
          if (!editing) return;
          const { id, fields } = editing;
          void save.run(() => (id ? api.update(id, fields) : api.create(fields)), async () => {
            setEditing(null);
            await reload();
          });
        }}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error && <Alert severity="error">{save.error}</Alert>}
          {editing && <Form value={editing.fields} onChange={(fields) => setEditing({ ...editing, fields })} />}
        </Stack>
      </FormDialog>

      <ConfirmDialog
        open={removing !== null}
        title={text.removeTitle}
        message={removing?.tool_pattern}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={remove.busy}
        onClose={() => setRemoving(null)}
        onConfirm={() =>
          void remove.run(
            () => api.remove(removing!.id),
            async () => {
              setRemoving(null);
              await reload();
            },
          )
        }
      />
    </Stack>
  );
}
