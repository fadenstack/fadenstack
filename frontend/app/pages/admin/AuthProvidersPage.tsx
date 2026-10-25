/**
 * Sign-in providers: the identity providers (OIDC, SAML) and directories (LDAP, Active Directory) people
 * sign in with. A sign-in module keeps them; without one switched on, the page says so.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { adminGroups, adminUsers, type Group, type RbacRole } from "~/api/admin";
import { ProviderApiError, signInProviders, type SignInProvider } from "~/api/signInProviders";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { helpUrl } from "~/lib/pageHelp";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";

import { ProviderDialog } from "./auth-providers/ProviderDialog";

interface Loaded {
  providers: SignInProvider[];
  /** No sign-in module is switched on: there is nothing to keep providers in. */
  moduleOff: boolean;
  roles: RbacRole[];
  groups: Group[];
}

async function load(): Promise<Loaded> {
  // Roles and groups only fill the policy's pick lists; the page works without them.
  const [roles, groups] = await Promise.all([
    adminUsers.listRoles().catch(() => [] as RbacRole[]),
    adminGroups.list().catch(() => [] as Group[]),
  ]);
  try {
    return { providers: await signInProviders.list(), moduleOff: false, roles, groups };
  } catch (err: unknown) {
    if (err instanceof ProviderApiError && err.moduleOff) return { providers: [], moduleOff: true, roles, groups };
    throw err;
  }
}

export default function AuthProvidersPage() {
  const { t, i18n } = useTranslation();
  const { data, loading, error, refresh, setError } = useAsyncData(load, [], {
    initialValue: { providers: [], moduleOff: false, roles: [], groups: [] } as Loaded,
  });

  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<SignInProvider | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SignInProvider | null>(null);
  const [deleting, setDeleting] = useState(false);

  function openEditor(provider: SignInProvider | null) {
    setEditing(provider);
    setEditorOpen(true);
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await signInProviders.remove(deleteTarget.key);
      setDeleteTarget(null);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("auth_providers.failed_delete"));
    } finally {
      setDeleting(false);
    }
  }

  const columns: ColumnDef<SignInProvider>[] = [
    {
      key: "name",
      label: t("auth_providers.name"),
      sortable: true,
      render: (row) => (
        <Box>
          <Typography fontWeight={600}>{row.name}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
            {row.key}
          </Typography>
        </Box>
      ),
    },
    {
      key: "protocol",
      label: t("auth_providers.protocol"),
      render: (row) => <Chip label={t(`auth_providers.protocols.${row.protocol}`)} size="small" variant="outlined" />,
    },
    {
      key: "status",
      label: t("auth_providers.status"),
      render: (row) => (
        <Chip
          label={row.enabled ? t("auth_providers.enabled") : t("auth_providers.disabled")}
          size="small"
          color={row.enabled ? "success" : "default"}
        />
      ),
    },
    {
      key: "accounts",
      label: t("auth_providers.accounts"),
      render: (row) => (row.policy.create_account ? t("auth_providers.accounts_created") : t("auth_providers.accounts_existing")),
    },
    {
      key: "actions",
      label: "",
      render: (row) => (
        <Stack direction="row" spacing={0.5}>
          <IconButton
            size="small"
            aria-label={t("auth_providers.edit_title", { name: row.name })}
            title={t("auth_providers.edit_title", { name: row.name })}
            onClick={() => openEditor(row)}
          >
            <EditIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            color="error"
            aria-label={t("auth_providers.delete_title")}
            title={t("auth_providers.delete_title")}
            onClick={() => setDeleteTarget(row)}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
    },
  ];

  if (data.moduleOff) {
    return (
      <Box>
        <Typography variant="h5" fontWeight={700} sx={{ mb: 2 }}>
          {t("auth_providers.title")}
        </Typography>
        <Alert severity="info" data-testid="sign-in-module-off">
          {t("auth_providers.module_off")}{" "}
          <Link href={helpUrl("administer/sign-in", i18n.language)} target="_blank" rel="noopener">
            {t("auth_providers.module_off_help")}
          </Link>
        </Alert>
      </Box>
    );
  }

  return (
    <Box>
      <DataTable
        columns={columns}
        rows={data.providers}
        rowKey={(r) => r.id}
        loading={loading}
        error={error}
        emptyMessage={t("auth_providers.empty")}
        title={t("auth_providers.title")}
        searchPlaceholder={t("auth_providers.search_placeholder")}
        onRefresh={refresh}
        toolbarActions={
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => openEditor(null)}>
            {t("auth_providers.create")}
          </Button>
        }
      />

      <ProviderDialog
        open={editorOpen}
        stored={editing}
        roles={data.roles}
        groups={data.groups}
        onClose={() => setEditorOpen(false)}
        onSaved={() => {
          setEditorOpen(false);
          void refresh();
        }}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title={t("auth_providers.delete_title")}
        message={t("auth_providers.delete_confirm", { name: deleteTarget?.name })}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleting}
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </Box>
  );
}
