/**
 * Assignments (administrators): bind a skill to a tenant, workspace, project
 * or assistant so it comes in there without being picked.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DeleteIcon from "@mui/icons-material/Delete";

import { FormDialog } from "~/components/FormDialog";
import {
  createAssignment,
  deleteAssignment,
  listAssignments,
  type AssignmentTargetType,
  type SkillAssignment,
} from "~/api/skills";

const NEEDS_ID: AssignmentTargetType[] = ["workspace", "project", "assistant"];

export default function SkillAssignmentsPanel({ skillId }: { skillId: string }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<SkillAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [targetType, setTargetType] = useState<AssignmentTargetType>("tenant");
  const [targetId, setTargetId] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listAssignments(skillId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [skillId]);

  useEffect(() => {
    void load();
  }, [load]);

  const needsId = NEEDS_ID.includes(targetType);

  async function add() {
    setSaving(true);
    try {
      await createAssignment(skillId, {
        target_type: targetType,
        target_id: needsId ? targetId.trim() : null,
      });
      setOpen(false);
      setTargetId("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function remove(assignmentId: string) {
    try {
      await deleteAssignment(skillId, assignmentId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Stack spacing={2} sx={{ maxWidth: 720 }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary">
        {t("skills.assignments_hint")}
      </Typography>
      <div>
        <Button variant="outlined" size="small" onClick={() => setOpen(true)}>
          {t("skills.add_assignment")}
        </Button>
      </div>
      {loading ? (
        <CircularProgress size={24} />
      ) : items.length === 0 ? (
        <Typography color="text.secondary">{t("skills.no_assignments")}</Typography>
      ) : (
        <List dense>
          {items.map((a) => (
            <ListItem
              key={a.id}
              divider
              secondaryAction={
                <IconButton
                  edge="end"
                  color="error"
                  size="small"
                  aria-label={t("common.delete")}
                  onClick={() => void remove(a.id)}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              }
            >
              <ListItemText
                primary={`${t(`skills.scope_${a.target_type}`)}${a.target_id ? `: ${a.target_id}` : ""}`}
                secondary={
                  a.priority_override != null
                    ? t("skills.priority_override", { value: a.priority_override })
                    : undefined
                }
              />
            </ListItem>
          ))}
        </List>
      )}
      <FormDialog
        open={open}
        title={t("skills.add_assignment")}
        loading={saving}
        submitLabel={t("skills.assign")}
        cancelLabel={t("common.cancel")}
        submitDisabled={needsId && !targetId.trim()}
        onSubmit={() => void add()}
        onClose={() => setOpen(false)}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label={t("skills.target_type")}
            select
            value={targetType}
            onChange={(e) => setTargetType(e.target.value as AssignmentTargetType)}
            fullWidth
          >
            {(["tenant", "global", "workspace", "project", "assistant"] as const).map((v) => (
              <MenuItem key={v} value={v}>
                {t(`skills.scope_${v}`)}
              </MenuItem>
            ))}
          </TextField>
          {needsId && (
            <TextField
              label={t("skills.target_id")}
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              fullWidth
              required
            />
          )}
        </Stack>
      </FormDialog>
    </Stack>
  );
}
