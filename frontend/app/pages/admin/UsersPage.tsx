import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { adminGroups, adminUsers, hasDeveloperMode, type AdminUser, type Group, type RbacRole } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { UserApiTokensDialog } from "./UserApiTokensDialog";
import { FormDialog } from "~/components/FormDialog";
import { DeveloperModeDialog } from "~/components/users/DeveloperModeDialog";
import { formatDateTime } from "~/lib/dates";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormGroup from "@mui/material/FormGroup";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import CodeIcon from "@mui/icons-material/Code";
import DeleteIcon from "@mui/icons-material/Delete";
import HowToRegIcon from "@mui/icons-material/HowToReg";
import KeyIcon from "@mui/icons-material/Key";
import PersonOffIcon from "@mui/icons-material/PersonOff";

export default function UsersPage() {
  const { t } = useTranslation();

  // ── Data loading via useAsyncData ──
  const {
    data: { users, roles, groups },
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    async () => {
      const [allUsers, allRoles, allGroups] = await Promise.all([
        adminUsers.list(),
        adminUsers.listRoles(),
        adminGroups.list(),
      ]);
      return { users: allUsers, roles: allRoles, groups: allGroups };
    },
    [],
    { initialValue: { users: [] as AdminUser[], roles: [] as RbacRole[], groups: [] as Group[] } },
  );

  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [developerModeOf, setDeveloperModeOf] = useState<AdminUser | null>(null);
  const [suspendTarget, setSuspendTarget] = useState<AdminUser | null>(null);
  const [tokensOf, setTokensOf] = useState<AdminUser | null>(null);
  const [suspending, setSuspending] = useState(false);
  const [selectedRoleIds, setSelectedRoleIds] = useState<string[]>([]);
  const [selectedUsageGroupId, setSelectedUsageGroupId] = useState<string>("");
  const [saving, setSaving] = useState(false);

  // Create user dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newSuperuser, setNewSuperuser] = useState(false);
  const [newRoleIds, setNewRoleIds] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  // Delete user dialog
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);
  const [deleting, setDeleting] = useState(false);

  function openEdit(user: AdminUser) {
    setEditing(user);
    setSelectedRoleIds(user.roles.map((role) => role.id));
    setSelectedUsageGroupId(user.usage_group_id ?? "");
  }

  function toggleRole(roleId: string) {
    setSelectedRoleIds((prev) =>
      prev.includes(roleId) ? prev.filter((id) => id !== roleId) : [...prev, roleId],
    );
  }

  async function saveRoles() {
    if (!editing) return;
    setSaving(true);
    try {
      await adminUsers.setUserRoles(editing.id, selectedRoleIds);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("users.failed_update_roles"));
      setSaving(false);
      return;
    }
    try {
      const nextGroup = selectedUsageGroupId || null;
      if (nextGroup !== (editing.usage_group_id ?? null)) {
        await adminUsers.setUsageGroup(editing.id, nextGroup);
      }
      setEditing(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("users.failed_update_usage_group"));
    } finally {
      setSaving(false);
    }
  }

  function openCreate() {
    setNewEmail("");
    setNewPassword("");
    setNewSuperuser(false);
    setNewRoleIds([]);
    setCreateOpen(true);
  }

  function toggleNewRole(roleId: string) {
    setNewRoleIds((prev) =>
      prev.includes(roleId) ? prev.filter((id) => id !== roleId) : [...prev, roleId],
    );
  }

  async function handleCreate() {
    setCreating(true);
    setError(null);
    try {
      await adminUsers.createUser({
        email: newEmail,
        password: newPassword,
        is_superuser: newSuperuser,
        role_ids: newRoleIds,
      });
      setCreateOpen(false);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("users.failed_create"));
    } finally {
      setCreating(false);
    }
  }

  async function setActive(target: AdminUser, active: boolean) {
    setSuspending(true);
    setError(null);
    try {
      await adminUsers.setActive(target.id, active);
      setSuspendTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("users.suspend.failed"));
    } finally {
      setSuspending(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await adminUsers.deleteUser(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("users.failed_delete"));
    } finally {
      setDeleting(false);
    }
  }

  const rolePermissionCount = useMemo(() => {
    const map: Record<string, number> = {};
    for (const role of roles) map[role.id] = role.permissions.length;
    return map;
  }, [roles]);

  const columns: ColumnDef<AdminUser>[] = [
    {
      key: "email",
      label: t("users.user"),
      sortable: true,
      sortValue: (u) => u.email,
      searchValue: (u) => u.email,
      render: (u) => (
        <>
          <Typography fontWeight={600}>{u.email}</Typography>
          <Typography variant="caption" color="text.secondary" fontFamily="monospace">
            {u.id}
          </Typography>
        </>
      ),
      minWidth: 320,
    },
    {
      key: "flags",
      label: t("users.flags"),
      searchValue: (u) => `${u.is_active} ${u.is_superuser} ${u.is_verified} ${hasDeveloperMode(u) ? "developer" : ""}`,
      render: (u) => (
        <Stack direction="row" spacing={0.8} flexWrap="wrap" useFlexGap>
          {u.is_superuser && <Chip size="small" color="warning" label={t("users.superuser")} />}
          {u.is_active ? (
            <Chip size="small" color="success" label={t("common.active")} />
          ) : (
            <Chip size="small" color="default" label={t("common.inactive")} />
          )}
          {u.is_verified ? (
            <Chip size="small" color="info" label={t("users.verified")} />
          ) : (
            <Chip size="small" color="default" label={t("users.unverified")} />
          )}
          {hasDeveloperMode(u) && (
            <Chip
              size="small"
              color="secondary"
              variant="outlined"
              icon={<CodeIcon />}
              label={t("users.developer_mode.chip")}
              title={
                u.developer_mode_until
                  ? t("users.developer_mode.chip_until", { until: formatDateTime(u.developer_mode_until) })
                  : undefined
              }
            />
          )}
        </Stack>
      ),
      minWidth: 260,
    },
    {
      key: "roles",
      label: t("users.roles"),
      searchValue: (u) => u.roles.map((r) => r.name).join(" "),
      render: (u) => (
        <Stack direction="row" spacing={0.8} flexWrap="wrap" useFlexGap>
          {u.roles.length ? (
            u.roles.map((r) => <Chip key={r.id} size="small" label={r.name} />)
          ) : (
            <Typography variant="body2" color="text.secondary">{t("users.no_roles")}</Typography>
          )}
        </Stack>
      ),
      minWidth: 260,
    },
    {
      key: "usage_group",
      label: t("users.usage_group"),
      sortable: true,
      sortValue: (u) => u.usage_group_name ?? "",
      searchValue: (u) => u.usage_group_name ?? "",
      render: (u) =>
        u.usage_group_name ? (
          <Chip size="small" variant="outlined" label={u.usage_group_name} />
        ) : (
          <Typography variant="body2" color="text.secondary">{t("users.usage_group_none")}</Typography>
        ),
      minWidth: 160,
    },
    {
      key: "permissions",
      label: t("users.permissions"),
      sortable: true,
      sortValue: (u) => u.permissions.length,
      searchValue: (u) => u.permissions.map((p) => `${p.resource}:${p.action}`).join(" "),
      render: (u) => <Typography>{u.permissions.length}</Typography>,
      minWidth: 120,
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (u) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          <Button size="small" variant="outlined" onClick={() => openEdit(u)}>
            {t("users.edit_roles")}
          </Button>
          <Tooltip title={t("users.developer_mode.title")}>
            <IconButton size="small" onClick={() => setDeveloperModeOf(u)}>
              <CodeIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("users.tokens.action")}>
            <IconButton size="small" data-user-tokens={u.id} onClick={() => setTokensOf(u)}>
              <KeyIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          {u.is_active ? (
            <Tooltip title={t("users.suspend.action")}>
              <IconButton size="small" data-user-suspend={u.id} onClick={() => setSuspendTarget(u)}>
                <PersonOffIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          ) : (
            <Tooltip title={t("users.suspend.reactivate")}>
              <IconButton
                size="small"
                color="success"
                data-user-reactivate={u.id}
                disabled={suspending}
                onClick={() => void setActive(u, true)}
              >
                <HowToRegIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <IconButton size="small" color="error" onClick={() => setDeleteTarget(u)} title={t("common.delete")}>
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
      minWidth: 240,
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
        title={t("users.title")}
        rows={users}
        columns={columns}
        rowKey={(u) => u.id}
        loading={loading}
        onRefresh={load}
        emptyMessage={t("users.empty")}
        searchPlaceholder={t("users.search_placeholder")}
        toolbarActions={
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={openCreate}
            size="small"
            data-tour-id="users.create"
          >
            {t("users.create")}
          </Button>
        }
      />

      {/* ── Edit roles dialog ── */}
      <FormDialog
        open={!!editing}
        title={t("users.assign_roles")}
        loading={saving}
        submitLabel={t("common.save")}
        cancelLabel={t("common.cancel")}
        onSubmit={saveRoles}
        onClose={() => setEditing(null)}
      >
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {editing?.email}
          </Typography>
          <FormGroup>
            {roles.map((role) => (
              <FormControlLabel
                key={role.id}
                control={
                  <Checkbox
                    checked={selectedRoleIds.includes(role.id)}
                    onChange={() => toggleRole(role.id)}
                    disabled={saving}
                  />
                }
                label={t("users.role_permissions", {
                  name: role.name,
                  count: rolePermissionCount[role.id] ?? 0,
                })}
              />
            ))}
          </FormGroup>
          {!roles.length && (
            <Box sx={{ mt: 1 }}>
              <Typography variant="body2" color="text.secondary">{t("users.no_roles_available")}</Typography>
            </Box>
          )}
          <TextField
            select
            fullWidth
            size="small"
            sx={{ mt: 2 }}
            label={t("users.usage_group")}
            value={selectedUsageGroupId}
            onChange={(e) => setSelectedUsageGroupId(e.target.value)}
            helperText={t("users.usage_group_help")}
            disabled={saving}
          >
            <MenuItem value="">{t("users.usage_group_none")}</MenuItem>
            {groups.map((g) => (
              <MenuItem key={g.id} value={g.id}>
                {g.name}
              </MenuItem>
            ))}
          </TextField>
      </FormDialog>

      <DeveloperModeDialog
        user={developerModeOf}
        onClose={() => setDeveloperModeOf(null)}
        onSaved={() => {
          setDeveloperModeOf(null);
          void load();
        }}
      />

      {/* ── Create user dialog ── */}
      <FormDialog
        open={createOpen}
        title={t("users.create_title")}
        loading={creating}
        submitLabel={t("users.create")}
        cancelLabel={t("common.cancel")}
        submitDisabled={!newEmail.trim() || newPassword.length < 6}
        onSubmit={handleCreate}
        onClose={() => setCreateOpen(false)}
      >
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Stack spacing={2} data-tour-id="users.new.account">
              <TextField
                label={t("auth.email")}
                type="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                fullWidth
                required
                disabled={creating}
              />
              <TextField
                label={t("auth.password")}
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                fullWidth
                required
                disabled={creating}
                inputProps={{ minLength: 6 }}
              />
            </Stack>
            <FormControlLabel
              control={
                <Switch
                  checked={newSuperuser}
                  onChange={(e) => setNewSuperuser(e.target.checked)}
                  disabled={creating}
                />
              }
              label={t("users.superuser")}
            />
            <Stack spacing={1} data-tour-id="users.new.roles">
              <Typography variant="subtitle2">{t("users.assign_roles")}</Typography>
              <FormGroup>
                {roles.map((role) => (
                  <FormControlLabel
                    key={role.id}
                    control={
                      <Checkbox
                        size="small"
                        checked={newRoleIds.includes(role.id)}
                        onChange={() => toggleNewRole(role.id)}
                        disabled={creating}
                      />
                    }
                    label={role.name}
                  />
                ))}
              </FormGroup>
            </Stack>
          </Stack>
      </FormDialog>

      <UserApiTokensDialog user={tokensOf} onClose={() => setTokensOf(null)} />

      {/* ── Suspend user dialog ── */}
      <ConfirmDialog
        open={!!suspendTarget}
        title={t("users.suspend.title")}
        message={t("users.suspend.confirm", { email: suspendTarget?.email ?? "" })}
        confirmLabel={t("users.suspend.action")}
        cancelLabel={t("common.cancel")}
        loading={suspending}
        onConfirm={() => suspendTarget && void setActive(suspendTarget, false)}
        onClose={() => setSuspendTarget(null)}
      />

      {/* ── Delete user dialog ── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("users.delete_title")}
        message={t("users.delete_confirm", { email: deleteTarget?.email ?? "" })}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleting}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
