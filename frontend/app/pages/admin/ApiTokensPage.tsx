/**
 * Access Control → API tokens: everyone's API tokens, who owns each, and how it is used. Every request made
 * with a token is recorded (when, from which address, with which client, on which route), so tokens do not
 * become a way around the organisation's overview. A token can be revoked here; the gateway refuses it at
 * the next request.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";

import { adminUsers, type AdminApiTokenInfo } from "~/api/admin";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { TokenLastUse, TokenUsesDialog } from "~/components/users/ApiTokenUses";
import { useAsyncData } from "~/lib/useAsyncData";

type State = "active" | "expired" | "revoked";

function stateOf(token: AdminApiTokenInfo): State {
  if (token.revoked_at) return "revoked";
  if (token.expires_at && new Date(token.expires_at).getTime() < Date.now()) return "expired";
  return "active";
}

export default function ApiTokensPage() {
  const { t, i18n } = useTranslation();
  const formatDate = (iso: string) => new Date(iso).toLocaleDateString(i18n.language || undefined);
  const { data: tokens, loading, error, refresh, setError } = useAsyncData(
    () => adminUsers.listAllApiTokens(),
    [],
    { initialValue: [] as AdminApiTokenInfo[] },
  );
  const [showEnded, setShowEnded] = useState(false);
  const [showing, setShowing] = useState<AdminApiTokenInfo | null>(null);
  const [revoking, setRevoking] = useState<AdminApiTokenInfo | null>(null);
  const [busy, setBusy] = useState(false);

  const rows = useMemo(
    () => (showEnded ? tokens : tokens.filter((token) => stateOf(token) === "active")),
    [tokens, showEnded],
  );

  async function revoke() {
    if (!revoking) return;
    setBusy(true);
    try {
      await adminUsers.revokeUserApiToken(revoking.user_id, revoking.id);
      setRevoking(null);
      refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.error_unexpected"));
    } finally {
      setBusy(false);
    }
  }

  const columns: ColumnDef<AdminApiTokenInfo>[] = [
    {
      key: "owner",
      label: t("api_tokens.owner"),
      sortable: true,
      sortValue: (row) => row.email,
      searchValue: (row) => row.email,
      render: (row) => <Typography variant="body2">{row.email}</Typography>,
    },
    {
      key: "name",
      label: t("api_tokens.name"),
      sortable: true,
      sortValue: (row) => row.name ?? "",
      searchValue: (row) => row.name ?? "",
      render: (row) => (
        <Stack spacing={0.25}>
          <Typography variant="body2" fontWeight={600}>
            {row.name || t("profile.unnamed_token")}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {t("profile.token_created", { date: formatDate(row.created_at) })}
            {" · "}
            {row.expires_at
              ? t("profile.token_expires", { date: formatDate(row.expires_at) })
              : t("profile.token_never_expires")}
          </Typography>
        </Stack>
      ),
    },
    {
      key: "last_used",
      label: t("api_tokens.last_used"),
      sortable: true,
      sortValue: (row) => row.last_used_at ?? "",
      searchValue: (row) => `${row.last_used_address ?? ""} ${row.last_used_client ?? ""}`,
      render: (row) => <TokenLastUse token={row} />,
    },
    {
      key: "state",
      label: t("api_tokens.state"),
      sortable: true,
      sortValue: (row) => stateOf(row),
      render: (row) => {
        const state = stateOf(row);
        return state === "active" ? (
          <Chip size="small" color="success" variant="outlined" label={t("api_tokens.state_active")} />
        ) : (
          <Chip size="small" label={t(state === "revoked" ? "profile.token_revoked" : "profile.token_expired")} />
        );
      },
    },
    {
      key: "actions",
      label: "",
      align: "right",
      render: (row) => (
        <Stack direction="row" spacing={1} justifyContent="flex-end">
          <Button size="small" onClick={() => setShowing(row)}>
            {t("api_tokens.show_uses")}
          </Button>
          {stateOf(row) === "active" && (
            <Button size="small" color="error" onClick={() => setRevoking(row)}>
              {t("profile.revoke_token")}
            </Button>
          )}
        </Stack>
      ),
    },
  ];

  return (
    <>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t("api_tokens.description")}
      </Typography>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        loading={loading}
        error={error}
        emptyMessage={t("api_tokens.none")}
        title={t("api_tokens.title")}
        searchPlaceholder={t("api_tokens.search")}
        onRefresh={refresh}
        toolbarActions={
          <FormControlLabel
            control={<Switch size="small" checked={showEnded} onChange={(e) => setShowEnded(e.target.checked)} />}
            label={t("api_tokens.show_ended")}
          />
        }
      />
      <TokenUsesDialog token={showing} load={adminUsers.listApiTokenUses} onClose={() => setShowing(null)} />
      <ConfirmDialog
        open={revoking !== null}
        title={t("profile.revoke_token")}
        message={t("api_tokens.revoke_confirm", {
          name: revoking?.name || t("profile.unnamed_token"),
          email: revoking?.email ?? "",
        })}
        confirmLabel={t("profile.revoke_token")}
        cancelLabel={t("common.cancel")}
        loading={busy}
        onConfirm={() => void revoke()}
        onClose={() => setRevoking(null)}
      />
    </>
  );
}
