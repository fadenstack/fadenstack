import { useState } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { FormDialog } from "~/components/FormDialog";
import { useAsyncData } from "~/lib/useAsyncData";
import {
  getServer,
  listServerTools,
  refreshServer,
  testServer,
  updateServer,
  updateTool,
  type MCPServerDetail,
  type MCPToolDetail,
  type MCPTestResult,
  type PIIMode,
} from "~/api/mcp";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import EditIcon from "@mui/icons-material/Edit";
import RefreshIcon from "@mui/icons-material/Refresh";
import ScienceIcon from "@mui/icons-material/Science";

import { MCPSettingsPanel } from "./MCPSettingsPanel";
import { NO_EDITS, SecretPairsEditor } from "./SecretPairsEditor";
import { commandLine, isHttp, serverPatch, type EditForm } from "./serverForm";

const STATUS_COLOR: Record<
  string,
  "success" | "warning" | "error" | "default" | "info"
> = {
  active: "success",
  degraded: "warning",
  error: "error",
  disconnected: "default",
  disabled: "default",
  registering: "info",
};

function editFormFor(server: MCPServerDetail): EditForm {
  return {
    url: server.url ?? "",
    command: commandLine(server),
    timeoutSec: server.timeout_sec,
    piiMode: server.pii_mode,
    headers: NO_EDITS,
    env: NO_EDITS,
  };
}

export default function MCPServerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const {
    data: server,
    loading: serverLoading,
    error: serverError,
    refresh: reloadServer,
    setError,
  } = useAsyncData(() => getServer(id!), [id], {
    initialValue: null as MCPServerDetail | null,
  });

  const {
    data: tools,
    loading: toolsLoading,
    refresh: reloadTools,
  } = useAsyncData(() => listServerTools(id!), [id], {
    initialValue: [] as MCPToolDetail[],
  });

  const [testResult, setTestResult] = useState<MCPTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  async function handleRefresh() {
    try {
      await refreshServer(id!);
      await Promise.all([reloadServer(), reloadTools()]);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.refresh_failed"));
      // The service kept the reason on the server; show it there.
      await reloadServer(true);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testServer(id!);
      setTestResult(result);
    } catch (err: unknown) {
      setTestResult({
        status: "error",
        tools_found: 0,
        message: err instanceof Error ? err.message : t("mcp.test_failed"),
      });
    } finally {
      setTesting(false);
    }
  }

  async function handleToggleEnabled() {
    if (!server) return;
    try {
      await updateServer(id!, { enabled: !server.enabled });
      await reloadServer();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.update_failed"));
    }
  }

  async function handleToggleTool(tool: MCPToolDetail) {
    try {
      await updateTool(tool.id, { enabled: !tool.enabled });
      await reloadTools();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.update_failed"));
    }
  }

  function openEdit() {
    if (!server) return;
    setEditError(null);
    setEditForm(editFormFor(server));
  }

  async function handleSave() {
    if (!server || !editForm) return;
    const patch = serverPatch(server, editForm);
    if (Object.keys(patch).length === 0) {
      setEditForm(null);
      return;
    }
    setSaving(true);
    setEditError(null);
    try {
      // Saved and reconnected; a failed connect comes back as last_error.
      await updateServer(id!, patch);
      setEditForm(null);
      setError(null);
      await Promise.all([reloadServer(), reloadTools()]);
    } catch (err: unknown) {
      setEditError(err instanceof Error ? err.message : t("common.update_failed"));
    } finally {
      setSaving(false);
    }
  }

  const toolColumns: ColumnDef<MCPToolDetail>[] = [
    {
      key: "name",
      label: t("mcp.tool_name", "Name"),
      sortable: true,
      sortValue: (tool) => tool.qualified_name,
      searchValue: (tool) => `${tool.qualified_name} ${tool.description}`,
      render: (tool) => (
        <>
          <Typography
            fontWeight={600}
            fontFamily="monospace"
            fontSize="0.85rem"
          >
            {tool.qualified_name}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {tool.description}
          </Typography>
        </>
      ),
      minWidth: 300,
    },
    {
      key: "version",
      label: t("mcp.version", "Version"),
      render: (tool) => (
        <Typography variant="body2" fontFamily="monospace">
          {tool.version}
        </Typography>
      ),
      minWidth: 80,
    },
    {
      key: "enabled",
      label: t("common.enabled", "Enabled"),
      sortable: true,
      sortValue: (tool) => (tool.enabled ? 1 : 0),
      render: (tool) => (
        <Switch
          size="small"
          checked={tool.enabled}
          onChange={() => handleToggleTool(tool)}
        />
      ),
      minWidth: 80,
    },
    {
      key: "last_seen",
      label: t("mcp.last_seen", "Last Seen"),
      sortable: true,
      sortValue: (tool) => tool.last_seen_at,
      render: (tool) => (
        <Typography variant="body2" color="text.secondary">
          {new Date(tool.last_seen_at).toLocaleString()}
        </Typography>
      ),
      minWidth: 160,
    },
  ];

  if (serverLoading && !server) {
    return <Typography>{t("common.loading", "Loading…")}</Typography>;
  }

  const http = server ? isHttp(server.transport) : false;
  const secretNames = server ? (http ? server.header_names : server.env_names) : [];

  return (
    <Box>
      {/* A failed refresh says the same as last_error; say it once. */}
      {serverError && serverError !== server?.last_error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {serverError}
        </Alert>
      )}

      {server?.last_error && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={openEdit}>
              {t("common.edit")}
            </Button>
          }
        >
          <AlertTitle>{t("mcp.last_error")}</AlertTitle>
          {server.last_error}
        </Alert>
      )}

      {testResult && (
        <Alert
          severity={testResult.status === "error" ? "error" : "success"}
          sx={{ mb: 2 }}
          onClose={() => setTestResult(null)}
        >
          {testResult.message}
          {testResult.status !== "error" &&
            ` ${t("mcp.tools_found", { count: testResult.tools_found })}`}
        </Alert>
      )}

      {/* ── Header ── */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
        <Button
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate("/admin/mcp/servers")}
          size="small"
        >
          {t("common.back", "Back")}
        </Button>
        <Typography variant="h5" fontWeight={700} sx={{ flex: 1 }}>
          {server?.name ?? "…"}
        </Typography>
        <Button
          startIcon={<EditIcon />}
          onClick={openEdit}
          disabled={!server}
          size="small"
          variant="outlined"
        >
          {t("common.edit")}
        </Button>
        <Button
          startIcon={<RefreshIcon />}
          onClick={handleRefresh}
          size="small"
          variant="outlined"
        >
          {t("mcp.refresh", "Refresh")}
        </Button>
        <Button
          startIcon={<ScienceIcon />}
          onClick={handleTest}
          disabled={testing}
          size="small"
          variant="outlined"
        >
          {t("mcp.test", "Test")}
        </Button>
      </Stack>

      {/* ── Server info card ── */}
      {server && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Grid container spacing={3}>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.status", "Status")}
                </Typography>
                <Box sx={{ mt: 0.5 }}>
                  <Chip
                    label={server.status}
                    color={STATUS_COLOR[server.status] ?? "default"}
                    size="small"
                  />
                </Box>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.transport", "Transport")}
                </Typography>
                <Typography>{server.transport.toUpperCase()}</Typography>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.tool_prefix", "Tool Prefix")}
                </Typography>
                <Typography fontFamily="monospace">
                  {server.tool_prefix}
                </Typography>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.pii_mode", "PII Mode")}
                </Typography>
                <Typography>{server.pii_mode}</Typography>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("common.enabled", "Enabled")}
                </Typography>
                <Box>
                  <Switch
                    checked={server.enabled}
                    onChange={handleToggleEnabled}
                    size="small"
                  />
                </Box>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.timeout", "Timeout")}
                </Typography>
                <Typography>{server.timeout_sec}s</Typography>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {http ? t("mcp.headers") : t("mcp.env")}
                </Typography>
                <Typography fontFamily="monospace" fontSize="0.85rem">
                  {secretNames.length ? secretNames.join(", ") : t("mcp.none_set")}
                </Typography>
              </Grid>
              {server.url && (
                <Grid size={{ xs: 12, sm: 6 }}>
                  <Typography variant="caption" color="text.secondary">
                    {t("mcp.url", "URL")}
                  </Typography>
                  <Typography fontFamily="monospace" fontSize="0.85rem">
                    {server.url}
                  </Typography>
                </Grid>
              )}
              {server.command && (
                <Grid size={{ xs: 12, sm: 6 }}>
                  <Typography variant="caption" color="text.secondary">
                    {t("mcp.command", "Command")}
                  </Typography>
                  <Typography fontFamily="monospace" fontSize="0.85rem">
                    {commandLine(server)}
                  </Typography>
                </Grid>
              )}
            </Grid>
          </CardContent>
        </Card>
      )}

      <Divider sx={{ my: 2 }} />

      {/* ── Provider settings, for a server that publishes a schema ── */}
      {server?.has_settings && <MCPSettingsPanel serverId={id!} />}

      {/* ── Tools table ── */}
      <DataTable
        title={t("mcp.tools_title", "Discovered Tools")}
        rows={tools}
        columns={toolColumns}
        rowKey={(tool) => tool.id}
        loading={toolsLoading}
        onRefresh={reloadTools}
        emptyMessage={t("mcp.no_tools", "No tools discovered yet.")}
        searchPlaceholder={t("mcp.search_tools", "Search tools…")}
      />

      {/* ── Edit ── */}
      {server && editForm && (
        <FormDialog
          open
          title={t("mcp.edit_server", { name: server.name })}
          loading={saving}
          submitLabel={t("common.save")}
          cancelLabel={t("common.cancel")}
          submitDisabled={http ? !editForm.url.trim() : !editForm.command.trim()}
          onSubmit={handleSave}
          onClose={() => setEditForm(null)}
        >
          <Stack spacing={2} sx={{ mt: 1 }}>
            {editError && <Alert severity="error">{editError}</Alert>}
            {http ? (
              <TextField
                label={t("mcp.server_url")}
                value={editForm.url}
                onChange={(e) => setEditForm({ ...editForm, url: e.target.value })}
                fullWidth
                required
                disabled={saving}
              />
            ) : (
              <TextField
                label={t("mcp.command")}
                value={editForm.command}
                onChange={(e) => setEditForm({ ...editForm, command: e.target.value })}
                fullWidth
                required
                disabled={saving}
                helperText={t("mcp.command_help")}
              />
            )}
            <TextField
              label={t("mcp.timeout_sec")}
              type="number"
              value={editForm.timeoutSec}
              onChange={(e) =>
                setEditForm({ ...editForm, timeoutSec: Number(e.target.value) })
              }
              fullWidth
              disabled={saving}
              inputProps={{ min: 5, max: 600 }}
              helperText={t("mcp.timeout_help")}
            />
            <TextField
              label={t("mcp.pii_mode")}
              select
              value={editForm.piiMode}
              onChange={(e) =>
                setEditForm({ ...editForm, piiMode: e.target.value as PIIMode })
              }
              fullWidth
              disabled={saving}
            >
              <MenuItem value="allow">{t("mcp.pii_allow")}</MenuItem>
              <MenuItem value="redact">{t("mcp.pii_redact")}</MenuItem>
              <MenuItem value="block">{t("mcp.pii_block")}</MenuItem>
            </TextField>
            {http ? (
              <SecretPairsEditor
                label={t("mcp.headers")}
                help={t("mcp.headers_help")}
                existing={server.header_names}
                edits={editForm.headers}
                onChange={(headers) => setEditForm({ ...editForm, headers })}
                namePlaceholder="Authorization"
                valuePlaceholder="Bearer …"
                addLabel={t("mcp.add_header")}
              />
            ) : (
              <SecretPairsEditor
                label={t("mcp.env")}
                help={t("mcp.env_help")}
                existing={server.env_names}
                edits={editForm.env}
                onChange={(env) => setEditForm({ ...editForm, env })}
                namePlaceholder="API_KEY"
                valuePlaceholder="…"
                addLabel={t("mcp.add_env")}
              />
            )}
            <Typography variant="caption" color="text.secondary">
              {t("mcp.edit_reconnects")}
            </Typography>
          </Stack>
        </FormDialog>
      )}
    </Box>
  );
}
