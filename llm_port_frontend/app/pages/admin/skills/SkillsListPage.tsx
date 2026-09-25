/**
 * Skills Hub (administrators): every skill in the tenant -- the
 * organization's and people's own -- with who sees it and what is live.
 */
import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";
import ArchiveIcon from "@mui/icons-material/Archive";
import DeleteIcon from "@mui/icons-material/Delete";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import PublishIcon from "@mui/icons-material/Publish";

import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import CreateSkillDialog from "~/components/skills/CreateSkillDialog";
import ImportSkillButton from "~/components/skills/ImportSkillButton";
import {
  SkillActivationChip,
  SkillStatusChip,
  SkillVisibilityChip,
} from "~/components/skills/SkillChips";
import { useAsyncData } from "~/lib/useAsyncData";
import { useCan } from "~/lib/useCan";
import {
  archiveSkill,
  deleteSkill,
  listSkills,
  publishSkill,
  skillRights,
  type SkillListView,
  type SkillSummary,
} from "~/api/skills";

export default function SkillsListPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const can = useCan();
  const rights = skillRights((p) => can(p));
  const [view, setView] = useState<SkillListView>("all");

  const {
    data: skills,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(async () => (await listSkills({ view, limit: 500 })).items, [view], {
    initialValue: [] as SkillSummary[],
  });

  const [deleteTarget, setDeleteTarget] = useState<SkillSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteSkill(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.delete_failed"));
    } finally {
      setDeleting(false);
    }
  }

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const columns: ColumnDef<SkillSummary>[] = [
    {
      key: "name",
      label: t("skills.name"),
      sortable: true,
      sortValue: (s) => s.name,
      searchValue: (s) => `${s.name} ${s.slug} ${s.description}`,
      render: (s) => (
        <>
          <Typography fontWeight={600}>{s.name}</Typography>
          <Typography variant="caption" color="text.secondary" fontFamily="monospace">
            ${s.slug}
          </Typography>
        </>
      ),
      minWidth: 200,
    },
    {
      key: "owner",
      label: t("skills.owner"),
      sortable: true,
      sortValue: (s) => (s.owner_id ? 1 : 0),
      render: (s) =>
        s.owner_id ? (
          <Chip size="small" variant="outlined" label={t("skills.owner_person")} title={s.owner_id} />
        ) : (
          <Chip size="small" variant="outlined" color="primary" label={t("skills.owner_org")} />
        ),
      minWidth: 110,
    },
    {
      key: "visibility",
      label: t("skills.visibility"),
      sortable: true,
      sortValue: (s) => s.visibility,
      render: (s) => <SkillVisibilityChip value={s.visibility} />,
      minWidth: 130,
    },
    {
      key: "activation",
      label: t("skills.activation"),
      sortable: true,
      sortValue: (s) => s.activation,
      render: (s) => <SkillActivationChip value={s.activation} />,
      minWidth: 120,
    },
    {
      key: "status",
      label: t("skills.status"),
      sortable: true,
      sortValue: (s) => `${s.status}-${s.published_version ?? 0}`,
      render: (s) => <SkillStatusChip skill={s} />,
      minWidth: 140,
    },
    {
      key: "tags",
      label: t("skills.tags"),
      render: (s) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap">
          {s.tags.slice(0, 3).map((tag) => (
            <Chip key={tag} size="small" label={tag} variant="outlined" />
          ))}
          {s.tags.length > 3 && <Chip size="small" label={`+${s.tags.length - 3}`} variant="outlined" />}
        </Stack>
      ),
      minWidth: 140,
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (s) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          <IconButton
            size="small"
            title={t("skills.view_details")}
            onClick={() => navigate(`/admin/skills/${s.id}`)}
          >
            <OpenInNewIcon fontSize="small" />
          </IconButton>
          {s.status !== "archived" && s.published_version !== s.latest_version && (
            <IconButton
              size="small"
              color="primary"
              title={t("skills.publish_latest", { version: s.latest_version })}
              onClick={() => void run(() => publishSkill(s.id))}
            >
              <PublishIcon fontSize="small" />
            </IconButton>
          )}
          {s.status === "published" && (
            <IconButton
              size="small"
              title={t("skills.archive")}
              onClick={() => void run(() => archiveSkill(s.id))}
            >
              <ArchiveIcon fontSize="small" />
            </IconButton>
          )}
          <IconButton
            size="small"
            color="error"
            title={t("common.delete")}
            onClick={() => setDeleteTarget(s)}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
      minWidth: 160,
    },
  ];

  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <DataTable
        title={t("skills.list_title")}
        rows={skills}
        columns={columns}
        rowKey={(s) => s.id}
        loading={loading}
        onRefresh={load}
        emptyMessage={t("skills.no_skills")}
        searchPlaceholder={t("skills.search")}
        toolbarActions={
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              select
              size="small"
              value={view}
              onChange={(e) => setView(e.target.value as SkillListView)}
              sx={{ minWidth: 170 }}
            >
              <MenuItem value="all">{t("skills.view_all")}</MenuItem>
              <MenuItem value="org">{t("skills.view_org")}</MenuItem>
              <MenuItem value="mine">{t("skills.view_mine")}</MenuItem>
              <MenuItem value="shared">{t("skills.view_shared")}</MenuItem>
            </TextField>
            <ImportSkillButton
              organization
              onImported={(s) => navigate(`/admin/skills/${s.id}`)}
              onError={(m) => setError(m)}
            />
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => setCreateOpen(true)}
              size="small"
            >
              {t("skills.create")}
            </Button>
          </Stack>
        }
      />

      <CreateSkillDialog
        open={createOpen}
        organization
        canPublishOrg={rights.publish || rights.manage}
        onClose={() => setCreateOpen(false)}
        onCreated={(s) => {
          setCreateOpen(false);
          navigate(`/admin/skills/${s.id}`);
        }}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title={t("skills.delete_title")}
        message={t("skills.delete_confirm", { name: deleteTarget?.name })}
        loading={deleting}
        confirmLabel={t("common.delete")}
        confirmColor="error"
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
