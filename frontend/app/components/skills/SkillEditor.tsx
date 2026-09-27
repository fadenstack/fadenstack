/**
 * One skill: its instructions, settings, who sees it, its versions.
 *
 * Shared by the admin pages and the users' own skill pages; what can be
 * changed follows the caller's access to the skill (``skill.access``) and
 * what RBAC lets them do (``rights``).
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ArchiveIcon from "@mui/icons-material/Archive";
import DeleteIcon from "@mui/icons-material/Delete";
import DownloadIcon from "@mui/icons-material/Download";
import PublishIcon from "@mui/icons-material/Publish";
import SaveIcon from "@mui/icons-material/Save";
import UnarchiveIcon from "@mui/icons-material/Unarchive";

import { ConfirmDialog } from "~/components/ConfirmDialog";
import {
  archiveSkill,
  deleteSkill,
  exportUrl,
  getSkill,
  publishSkill,
  restoreSkill,
  saveVersion,
  updateSkill,
  type SkillDetail,
  type SkillRights,
} from "~/api/skills";
import SkillAssignmentsPanel from "./SkillAssignmentsPanel";
import { SkillActivationChip, SkillStatusChip, SkillVisibilityChip } from "./SkillChips";
import {
  EMPTY_SKILL_FORM,
  SkillInstructionsFields,
  SkillSettingsFields,
  catalogChanges,
  contentChanged,
  manifestFromValues,
  valuesFromSkill,
  type SkillFormValues,
} from "./SkillForm";
import SkillHistoryPanel from "./SkillHistoryPanel";
import SkillSharingPanel from "./SkillSharingPanel";

const RANK = { read: 1, write: 2, owner: 3, admin: 4 } as const;

interface Props {
  skillId: string;
  rights: SkillRights;
  /** The admin pages: assignments are shown. */
  adminView?: boolean;
  /** Rendered before the title (a back button). */
  back?: ReactNode;
  onDeleted: () => void;
}

export default function SkillEditor({ skillId, rights, adminView, back, onDeleted }: Props) {
  const { t } = useTranslation();
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [values, setValues] = useState<SkillFormValues>(EMPTY_SKILL_FORM);
  const [tab, setTab] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [exportAnchor, setExportAnchor] = useState<HTMLElement | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const show = useCallback((s: SkillDetail) => {
    setSkill(s);
    setValues(valuesFromSkill(s));
  }, []);

  useEffect(() => {
    setLoading(true);
    getSkill(skillId)
      .then(show)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [skillId, show]);

  const onChange = useCallback(
    <K extends keyof SkillFormValues>(key: K, value: SkillFormValues[K]) =>
      setValues((prev) => ({ ...prev, [key]: value })),
    [],
  );

  const level = skill ? RANK[skill.access] : 0;
  const canEdit = level >= RANK.write;
  const isOwner = level >= RANK.owner;
  const isAdmin = skill?.access === "admin";
  const orgVisible = skill ? skill.visibility !== "private" : false;

  const dirtyContent = useMemo(
    () => (skill ? contentChanged(values, skill) : false),
    [skill, values],
  );
  const dirtyCatalog = useMemo(
    () => (skill ? Object.keys(catalogChanges(values, skill)).length > 0 : false),
    [skill, values],
  );
  const dirty = dirtyContent || dirtyCatalog;

  async function save(publish: boolean) {
    if (!skill) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      let current = skill;
      const changes = catalogChanges(values, skill);
      if (Object.keys(changes).length > 0) current = await updateSkill(skill.id, changes);
      if (dirtyContent) {
        const result = await saveVersion(skill.id, {
          description: values.description,
          body_markdown: values.body,
          manifest: manifestFromValues(values),
          base_version: skill.latest_version,
          publish,
          change_note: values.changeNote || undefined,
        });
        current = result.skill;
      } else if (publish && current.published_version !== current.latest_version) {
        current = await publishSkill(skill.id);
      }
      show(current);
      setNotice(publish ? t("skills.saved_published") : t("skills.saved_draft"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function lifecycle(action: "archive" | "restore" | "publish") {
    if (!skill) return;
    setError(null);
    try {
      const next =
        action === "archive"
          ? await archiveSkill(skill.id)
          : action === "restore"
            ? await restoreSkill(skill.id)
            : await publishSkill(skill.id);
      show(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    if (!skill) return;
    setDeleting(true);
    try {
      await deleteSkill(skill.id);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (!skill) {
    return (
      <Stack spacing={2}>
        {back}
        <Alert severity="error">{error || t("skills.not_found")}</Alert>
      </Stack>
    );
  }

  const pending =
    skill.published_version != null && skill.latest_version !== skill.published_version;
  const tabs: { key: string; label: string }[] = [
    { key: "instructions", label: t("skills.tab_editor") },
    { key: "settings", label: t("skills.tab_metadata") },
  ];
  if (isOwner) tabs.push({ key: "sharing", label: t("skills.tab_sharing") });
  if (canEdit) tabs.push({ key: "history", label: t("skills.tab_versions") });
  if (adminView && isAdmin) tabs.push({ key: "assignments", label: t("skills.tab_assignments") });
  const current = tabs[Math.min(tab, tabs.length - 1)].key;

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1, flexWrap: "wrap" }}>
        {back}
        <Typography variant="h5" sx={{ flexGrow: 1, minWidth: 0 }} noWrap>
          {skill.name}
        </Typography>
        <Tooltip title={t("skills.slug_hint")}>
          <Chip size="small" label={`$${skill.slug}`} sx={{ fontFamily: "monospace" }} />
        </Tooltip>
        <SkillStatusChip skill={skill} />
        <SkillVisibilityChip value={skill.visibility} />
        <SkillActivationChip value={skill.activation} />
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {skill.owner_id ? t("skills.owned_by_person") : t("skills.owned_by_org")}
        {" · "}
        {t(`skills.access_${skill.access}_label`)}
        {" · "}
        {t("skills.uses_30d", { count: skill.uses_30d })}
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {notice && !error && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      {pending && canEdit && !dirty && (
        <Alert
          severity="info"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={() => void lifecycle("publish")}>
              {t("skills.publish_latest", { version: skill.latest_version })}
            </Button>
          }
        >
          {t("skills.status_pending_hint", {
            latest: skill.latest_version,
            live: skill.published_version,
          })}
        </Alert>
      )}
      {!canEdit && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t("skills.read_only_hint")}
        </Alert>
      )}

      {/* Actions */}
      <Stack direction="row" spacing={1} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
        {canEdit && (
          <>
            <Button
              variant="contained"
              size="small"
              startIcon={<SaveIcon />}
              disabled={saving || !dirty || !values.name.trim() || !values.body.trim()}
              onClick={() => void save(false)}
            >
              {saving ? t("common.saving") : t("skills.save_draft")}
            </Button>
            <Button
              variant="outlined"
              size="small"
              startIcon={<PublishIcon />}
              disabled={
                saving ||
                !values.name.trim() ||
                !values.body.trim() ||
                (!dirty && skill.published_version === skill.latest_version && skill.status !== "draft")
              }
              onClick={() => void save(true)}
            >
              {t("skills.save_publish")}
            </Button>
          </>
        )}
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          onClick={(e) => setExportAnchor(e.currentTarget)}
        >
          {t("skills.export")}
        </Button>
        <Menu anchorEl={exportAnchor} open={!!exportAnchor} onClose={() => setExportAnchor(null)}>
          <MenuItem
            component="a"
            href={exportUrl(skill.id, "zip")}
            onClick={() => setExportAnchor(null)}
          >
            {t("skills.export_zip")}
          </MenuItem>
          <MenuItem
            component="a"
            href={exportUrl(skill.id, "md")}
            onClick={() => setExportAnchor(null)}
          >
            {t("skills.export_md")}
          </MenuItem>
        </Menu>
        <Box sx={{ flexGrow: 1 }} />
        {isOwner && skill.status !== "archived" && (
          <Button size="small" startIcon={<ArchiveIcon />} onClick={() => void lifecycle("archive")}>
            {t("skills.archive")}
          </Button>
        )}
        {isOwner && skill.status === "archived" && (
          <Button size="small" startIcon={<UnarchiveIcon />} onClick={() => void lifecycle("restore")}>
            {t("skills.restore")}
          </Button>
        )}
        {isOwner && (
          <IconButton
            color="error"
            size="small"
            aria-label={t("common.delete")}
            onClick={() => setDeleteOpen(true)}
          >
            <DeleteIcon />
          </IconButton>
        )}
      </Stack>

      <Divider />
      <Tabs
        value={Math.min(tab, tabs.length - 1)}
        onChange={(_, v) => setTab(v)}
        variant="scrollable"
        allowScrollButtonsMobile
      >
        {tabs.map((tb) => (
          <Tab key={tb.key} label={tb.label} />
        ))}
      </Tabs>

      <Box sx={{ py: 2 }}>
        {current === "instructions" && (
          <Stack spacing={2}>
            <SkillInstructionsFields
              values={values}
              onChange={onChange}
              readOnly={!canEdit}
              showChangeNote
            />
            {skill.files.length > 0 && (
              <Box>
                <Typography variant="subtitle2">{t("skills.files_title")}</Typography>
                <List dense>
                  {skill.files.map((f) => (
                    <ListItem key={f.path} disableGutters>
                      <ListItemText
                        primary={<Box component="span" sx={{ fontFamily: "monospace" }}>{f.path}</Box>}
                        secondary={t("skills.file_size", { size: f.size })}
                      />
                      {f.executable && (
                        <Tooltip title={t("skills.file_script_hint")}>
                          <Chip size="small" color="warning" variant="outlined" label={t("skills.file_script")} />
                        </Tooltip>
                      )}
                    </ListItem>
                  ))}
                </List>
              </Box>
            )}
          </Stack>
        )}
        {current === "settings" && (
          <SkillSettingsFields
            values={values}
            onChange={onChange}
            readOnly={!canEdit}
            canChangeActivation={isOwner}
            allowAlways={isAdmin || !orgVisible}
          />
        )}
        {current === "sharing" && (
          <SkillSharingPanel
            skill={skill}
            canShare={rights.share || isAdmin}
            canPublishOrg={rights.publish}
            isAdmin={isAdmin}
            onChanged={show}
          />
        )}
        {current === "history" && (
          <SkillHistoryPanel skill={skill} canPublish={canEdit} onChanged={show} />
        )}
        {current === "assignments" && <SkillAssignmentsPanel skillId={skill.id} />}
      </Box>

      <ConfirmDialog
        open={deleteOpen}
        title={t("skills.delete_title")}
        message={t("skills.delete_confirm", { name: skill.name })}
        loading={deleting}
        confirmLabel={t("common.delete")}
        confirmColor="error"
        onConfirm={() => void remove()}
        onClose={() => setDeleteOpen(false)}
      />
    </Box>
  );
}
