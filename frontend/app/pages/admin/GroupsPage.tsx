/**
 * Teams: their roles and members, and their place in the organisation's tree.
 *
 * The list is the tree (each team's sub-teams right below it, siblings by name), so it is not sorted by
 * column. A team gets one parent and a cost centre; the parent picker offers no team that would close a
 * cycle and marks those that would push the tree past six levels. A team that ended is archived: it keeps
 * its history (usage stays counted where it was), gets no new members, cannot be a home team and leaves
 * the pickers; archived teams show on demand and can be brought back. An archived team that still has
 * active sub-teams stays listed above them, marked archived, and stays their parent when one is edited.
 * Rights do not flow down the tree.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  adminAuthProviders,
  adminGroups,
  adminUsers,
  teamKeeper,
  type Group,
  type GroupMember,
  type RbacRole,
  type AdminUser,
} from "~/api/admin";
import { COST_CENTER_MAX, teamAdmin, teamErrorText } from "~/api/teamAdmin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";
import { roleLabel } from "~/lib/roleLabels";
import { activeWithAncestors, asTree, descendantsOf, MAX_TEAM_DEPTH, parentOptions, type TreeRow } from "~/lib/teamTree";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import ArchiveOutlinedIcon from "@mui/icons-material/ArchiveOutlined";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";
import PeopleIcon from "@mui/icons-material/People";
import PersonRemoveIcon from "@mui/icons-material/PersonRemove";
import SubdirectoryArrowRightIcon from "@mui/icons-material/SubdirectoryArrowRight";
import UnarchiveOutlinedIcon from "@mui/icons-material/UnarchiveOutlined";

/** The value of the parent picker for "no parent". */
const TOP = "";

export default function GroupsPage() {
  const { t, i18n } = useTranslation();

  // ── Data loading via useAsyncData ──
  const {
    data: { groups, roles, allUsers, providerNames },
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    async () => {
      const [g, r, u, p] = await Promise.all([
        adminGroups.list(),
        adminUsers.listRoles(),
        adminUsers.list(),
        // Only to name the provider that keeps a group; its key stands in when it cannot be had.
        adminAuthProviders.listPublic().catch(() => []),
      ]);
      return { groups: g, roles: r, allUsers: u, providerNames: new Map(p.map((x) => [x.key, x.name])) };
    },
    [],
    {
      initialValue: {
        groups: [] as Group[], roles: [] as RbacRole[], allUsers: [] as AdminUser[],
        providerNames: new Map<string, string>(),
      },
    },
  );

  const [showArchived, setShowArchived] = useState(false);
  const rows = useMemo(
    // Without the archived ones, still with those above an active team: a sub-team keeps its place.
    () => asTree(showArchived ? groups : activeWithAncestors(groups), i18n.language),
    [groups, showArchived, i18n.language],
  );
  const archivedCount = groups.filter((g) => g.archived_at).length;

  // Group editor dialog
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<Group | null>(null);
  const [groupName, setGroupName] = useState("");
  const [groupDesc, setGroupDesc] = useState("");
  const [groupParent, setGroupParent] = useState(TOP);
  const [groupCostCenter, setGroupCostCenter] = useState("");
  const [selectedRoleIds, setSelectedRoleIds] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<Group | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Archive dialog (bringing back needs no confirmation)
  const [archiveTarget, setArchiveTarget] = useState<Group | null>(null);
  const [archiving, setArchiving] = useState(false);

  // Members dialog
  const [membersGroup, setMembersGroup] = useState<Group | null>(null);
  const [members, setMembers] = useState<GroupMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(new Set());

  // ── Group editor ───────────────────────────────────────────────────

  function openCreate() {
    setEditingGroup(null);
    setGroupName("");
    setGroupDesc("");
    setGroupParent(TOP);
    setGroupCostCenter("");
    setSelectedRoleIds(new Set());
    setEditorError(null);
    setEditorOpen(true);
  }

  function openEdit(group: Group) {
    setEditingGroup(group);
    setGroupName(group.name);
    setGroupDesc(group.description ?? "");
    setGroupParent(group.parent_id ?? TOP);
    setGroupCostCenter(group.cost_center ?? "");
    setSelectedRoleIds(new Set(group.roles.map((r) => r.id)));
    setEditorError(null);
    setEditorOpen(true);
  }

  const parents = useMemo(
    () => parentOptions(groups, editingGroup?.id ?? null, i18n.language),
    [groups, editingGroup, i18n.language],
  );
  const parentAllowed = groupParent === TOP || parents.some((p) => p.team.id === groupParent && p.allowed);

  async function handleSave() {
    setSaving(true);
    setEditorError(null);
    try {
      const body = {
        name: groupName,
        description: groupDesc || undefined,
        role_ids: Array.from(selectedRoleIds),
        parent_id: groupParent || null,
        cost_center: groupCostCenter.trim() || null,
      };
      if (editingGroup) {
        await teamAdmin.update(editingGroup.id, body);
      } else {
        await teamAdmin.create(body);
      }
      setEditorOpen(false);
      await load();
    } catch (err: unknown) {
      setEditorError(teamErrorText(err, (key) => t(key)) || t("groups.failed_save"));
    } finally {
      setSaving(false);
    }
  }

  // ── Delete handler ─────────────────────────────────────────────────

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await adminGroups.delete(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("groups.failed_delete"));
    } finally {
      setDeleting(false);
    }
  }

  // ── Archive / bring back ───────────────────────────────────────────

  async function handleArchive() {
    if (!archiveTarget) return;
    setArchiving(true);
    try {
      await teamAdmin.archive(archiveTarget.id);
      setArchiveTarget(null);
      await load();
    } catch (err: unknown) {
      setError(teamErrorText(err, (key) => t(key)));
    } finally {
      setArchiving(false);
    }
  }

  async function handleUnarchive(group: Group) {
    try {
      await teamAdmin.unarchive(group.id);
      await load();
    } catch (err: unknown) {
      setError(teamErrorText(err, (key) => t(key)));
    }
  }

  // ── Members management ────────────────────────────────────────────

  async function openMembers(group: Group) {
    setMembersGroup(group);
    setMembersLoading(true);
    try {
      const m = await adminGroups.listMembers(group.id);
      setMembers(m);
    } catch {
      setMembers([]);
    } finally {
      setMembersLoading(false);
    }
  }

  async function handleRemoveMember(userId: string) {
    if (!membersGroup) return;
    try {
      await adminGroups.removeMember(membersGroup.id, userId);
      setMembers((prev) => prev.filter((m) => m.id !== userId));
      // Refresh group list for updated member_count
      await load();
    } catch {
      /* swallow */
    }
  }

  function openAddMembers() {
    if (!membersGroup) return;
    const currentIds = new Set(members.map((m) => m.id));
    setSelectedUserIds(currentIds);
    setAddMemberOpen(true);
  }

  async function handleSetMembers() {
    if (!membersGroup) return;
    try {
      const updated = await adminGroups.setMembers(
        membersGroup.id,
        Array.from(selectedUserIds),
      );
      setMembers(updated);
      setAddMemberOpen(false);
      await load();
    } catch {
      /* swallow */
    }
  }


  // ── Table columns ──────────────────────────────────────────────────
  // Not sortable: the order is the tree.

  const columns: ColumnDef<TreeRow<Group>>[] = [
    {
      key: "name",
      label: t("groups.name"),
      searchValue: ({ team }) => `${team.name} ${team.cost_center ?? ""}`,
      render: ({ team, depth }) => {
        const keeper = teamKeeper(team);
        return (
          <Stack
            direction="row"
            alignItems="flex-start"
            gap={0.5}
            sx={{ pl: (depth - 1) * 2.5 }}
            data-team-row={team.id}
            data-team-depth={depth}
          >
            {depth > 1 && (
              <SubdirectoryArrowRightIcon fontSize="small" sx={{ color: "text.disabled", mt: 0.25 }} aria-hidden />
            )}
            <Stack spacing={0.5} alignItems="flex-start" sx={{ minWidth: 0 }}>
              <Typography fontWeight={600} color={team.archived_at ? "text.secondary" : undefined}>
                {team.name}
              </Typography>
              <Stack direction="row" gap={0.5} flexWrap="wrap">
                {team.cost_center && (
                  <Chip size="small" variant="outlined" label={t("groups.tree.cost_center_chip", { code: team.cost_center })} />
                )}
                {team.archived_at && <Chip size="small" label={t("groups.tree.archived")} />}
                {keeper && (
                  <Tooltip title={t("groups.kept_by_help")}>
                    <Chip
                      size="small"
                      variant="outlined"
                      label={t("groups.kept_by", { provider: providerNames.get(keeper) ?? keeper })}
                    />
                  </Tooltip>
                )}
              </Stack>
            </Stack>
          </Stack>
        );
      },
    },
    {
      key: "description",
      label: t("groups.description"),
      render: ({ team }) => team.description ?? "—",
    },
    {
      key: "roles",
      label: t("groups.roles_count"),
      render: ({ team }) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap">
          {team.roles.map((r) => (
            <Chip key={r.id} label={roleLabel(r.name, t)} size="small" />
          ))}
          {team.roles.length === 0 && "—"}
        </Stack>
      ),
    },
    {
      key: "members",
      label: t("groups.members_count"),
      render: ({ team }) => team.member_count,
    },
    {
      key: "actions",
      label: "",
      render: ({ team }) => (
        <Stack direction="row" spacing={0.5}>
          <IconButton size="small" title={t("groups.members")} onClick={() => openMembers(team)}>
            <PeopleIcon fontSize="small" />
          </IconButton>
          <IconButton size="small" title={t("groups.edit_title")} onClick={() => openEdit(team)}>
            <EditIcon fontSize="small" />
          </IconButton>
          {team.archived_at ? (
            <IconButton
              size="small"
              title={t("groups.tree.unarchive")}
              aria-label={t("groups.tree.unarchive")}
              onClick={() => void handleUnarchive(team)}
              data-team-unarchive={team.id}
            >
              <UnarchiveOutlinedIcon fontSize="small" />
            </IconButton>
          ) : (
            <IconButton
              size="small"
              title={t("groups.tree.archive")}
              aria-label={t("groups.tree.archive")}
              onClick={() => setArchiveTarget(team)}
              data-team-archive={team.id}
            >
              <ArchiveOutlinedIcon fontSize="small" />
            </IconButton>
          )}
          <IconButton
            size="small"
            color="error"
            title={t("groups.delete_title")}
            onClick={() => setDeleteTarget(team)}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
    },
  ];

  const deleteHasSubTeams = deleteTarget ? descendantsOf(groups, deleteTarget.id).size > 0 : false;

  return (
    <Box>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.team.id}
        loading={loading}
        error={error}
        emptyMessage={t("groups.empty")}
        title={t("groups.title")}
        searchPlaceholder={t("groups.search_placeholder")}
        onRefresh={load}
        toolbarActions={
          <Stack direction="row" alignItems="center" gap={1}>
            {archivedCount > 0 && (
              <FormControlLabel
                control={<Switch size="small" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />}
                label={t("groups.tree.show_archived", { count: archivedCount })}
              />
            )}
            <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={openCreate}>
              {t("groups.create")}
            </Button>
          </Stack>
        }
      />

      {/* ── Create / Edit Group Dialog ─────────────────────────────── */}
      <FormDialog
        open={editorOpen}
        title={editingGroup ? t("groups.edit_title") : t("groups.create_title")}
        loading={saving}
        submitLabel={editingGroup ? t("common.save") : t("groups.create")}
        cancelLabel={t("common.cancel")}
        submitDisabled={!groupName.trim() || !parentAllowed || groupCostCenter.trim().length > COST_CENTER_MAX}
        onSubmit={handleSave}
        onClose={() => setEditorOpen(false)}
      >
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label={t("groups.name")}
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              required
              fullWidth
              size="small"
            />
            <TextField
              label={t("groups.description")}
              value={groupDesc}
              onChange={(e) => setGroupDesc(e.target.value)}
              fullWidth
              size="small"
              multiline
              rows={2}
            />
            <TextField
              select
              label={t("groups.tree.parent")}
              value={groupParent}
              onChange={(e) => setGroupParent(e.target.value)}
              helperText={t("groups.tree.parent_help", { max: MAX_TEAM_DEPTH })}
              fullWidth
              size="small"
              slotProps={{ htmlInput: { "data-team-parent": "" } }}
            >
              <MenuItem value={TOP}>{t("groups.tree.top")}</MenuItem>
              {parents.map(({ team, depth, allowed, archived }) => (
                <MenuItem key={team.id} value={team.id} disabled={!allowed} sx={{ pl: 2 + (depth - 1) * 2 }}>
                  <ListItemText
                    primary={team.name}
                    secondary={
                      archived
                        ? `${t("groups.tree.level", { level: depth })} · ${t("groups.tree.archived")}`
                        : allowed
                          ? t("groups.tree.level", { level: depth })
                          : t("groups.tree.too_deep_option", { max: MAX_TEAM_DEPTH })
                    }
                  />
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label={t("groups.tree.cost_center")}
              value={groupCostCenter}
              onChange={(e) => setGroupCostCenter(e.target.value)}
              helperText={t("groups.tree.cost_center_help", { max: COST_CENTER_MAX })}
              error={groupCostCenter.trim().length > COST_CENTER_MAX}
              fullWidth
              size="small"
              slotProps={{ htmlInput: { maxLength: COST_CENTER_MAX + 10 } }}
            />
            {editorError && <Alert severity="error">{editorError}</Alert>}
            <Typography variant="subtitle2">{t("groups.assign_roles")}</Typography>
            <Typography variant="caption" color="text.secondary">
              {t("groups.tree.roles_stay")}
            </Typography>
            <Box sx={{ maxHeight: 280, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, p: 1 }}>
              {roles.map((role) => (
                <FormControlLabel
                  key={role.id}
                  control={
                    <Checkbox
                      size="small"
                      checked={selectedRoleIds.has(role.id)}
                      onChange={() => {
                        setSelectedRoleIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(role.id)) next.delete(role.id);
                          else next.add(role.id);
                          return next;
                        });
                      }}
                    />
                  }
                  label={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <span>{roleLabel(role.name, t)}</span>
                      {role.is_builtin && <Chip label={t("roles.builtin")} size="small" variant="outlined" />}
                    </Stack>
                  }
                />
              ))}
            </Box>
          </Stack>
      </FormDialog>

      {/* ── Archive Confirmation Dialog ───────────────────────────── */}
      <ConfirmDialog
        open={!!archiveTarget}
        title={t("groups.tree.archive_title", { name: archiveTarget?.name })}
        message={t("groups.tree.archive_body")}
        confirmLabel={t("groups.tree.archive")}
        cancelLabel={t("common.cancel")}
        confirmColor="warning"
        loading={archiving}
        onConfirm={handleArchive}
        onClose={() => setArchiveTarget(null)}
      />

      {/* ── Delete Confirmation Dialog ────────────────────────────── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("groups.delete_title")}
        message={
          <>
            <Typography>
              {t("groups.delete_confirm", { name: deleteTarget?.name })}
            </Typography>
            {(deleteTarget?.member_count ?? 0) > 0 && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                {t("groups.delete_warning_members", { count: deleteTarget?.member_count })}
              </Alert>
            )}
            {deleteHasSubTeams && (
              <Alert severity="info" sx={{ mt: 1 }}>
                {t("groups.tree.delete_sub_teams")}
              </Alert>
            )}
          </>
        }
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleting}
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />

      {/* ── Members Management Dialog ─────────────────────────────── */}
      <Dialog
        open={!!membersGroup}
        onClose={() => setMembersGroup(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {t("groups.members")} — {membersGroup?.name}
        </DialogTitle>
        <DialogContent>
          {membersLoading ? (
            <Typography>{t("common.loading")}</Typography>
          ) : members.length === 0 ? (
            <Typography color="text.secondary">{t("groups.no_members")}</Typography>
          ) : (
            <List dense>
              {members.map((m) => (
                <ListItem
                  key={m.id}
                  secondaryAction={
                    <IconButton
                      edge="end"
                      size="small"
                      color="error"
                      title={t("groups.remove_member")}
                      onClick={() => handleRemoveMember(m.id)}
                    >
                      <PersonRemoveIcon fontSize="small" />
                    </IconButton>
                  }
                >
                  <ListItemText primary={m.email} />
                </ListItem>
              ))}
            </List>
          )}
        </DialogContent>
        <DialogActions>
          {/* An archived team gets no new members. */}
          {!membersGroup?.archived_at && (
            <Button startIcon={<AddIcon />} onClick={openAddMembers}>
              {t("groups.add_members")}
            </Button>
          )}
          <Button onClick={() => setMembersGroup(null)}>{t("common.close")}</Button>
        </DialogActions>
      </Dialog>

      {/* ── Add Members Dialog ────────────────────────────────────── */}
      <Dialog open={addMemberOpen} onClose={() => setAddMemberOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t("groups.manage_members")}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t("groups.add_members")}
          </Typography>
          <Box sx={{ maxHeight: 320, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, p: 1 }}>
            {allUsers.map((u) => (
              <FormControlLabel
                key={u.id}
                control={
                  <Checkbox
                    size="small"
                    checked={selectedUserIds.has(u.id)}
                    onChange={() => {
                      setSelectedUserIds((prev) => {
                        const next = new Set(prev);
                        if (next.has(u.id)) next.delete(u.id);
                        else next.add(u.id);
                        return next;
                      });
                    }}
                  />
                }
                label={u.email}
              />
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddMemberOpen(false)}>{t("common.cancel")}</Button>
          <Button variant="contained" onClick={handleSetMembers}>
            {t("common.save")}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
