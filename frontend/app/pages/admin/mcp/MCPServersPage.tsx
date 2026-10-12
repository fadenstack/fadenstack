import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";
import { useServices } from "~/lib/ServicesContext";
import { useAsyncData } from "~/lib/useAsyncData";
import {
  listServers,
  registerServer,
  deleteServer,
  refreshServer,
  scanForServers,
  type MCPServerSummary,
  type MCPTransportType,
  type PIIMode,
  type DiscoveredServer,
} from "~/api/mcp";
import {
  NO_EDITS,
  SecretPairsEditor,
  toSecretMap,
  type SecretEdits,
} from "./SecretPairsEditor";
import { PiiServiceWarning } from "./PiiServiceWarning";
import { isHttp, splitCommand } from "./serverForm";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import DeleteIcon from "@mui/icons-material/Delete";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import RadarIcon from "@mui/icons-material/Radar";
import RefreshIcon from "@mui/icons-material/Refresh";
import SettingsIcon from "@mui/icons-material/Settings";

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

export default function MCPServersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isModuleEnabled } = useServices();

  const {
    data: servers,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(() => listServers(), [], {
    initialValue: [] as MCPServerSummary[],
  });

  // Register dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTransport, setNewTransport] =
    useState<MCPTransportType>("streamable_http");
  const [newPrefix, setNewPrefix] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newCommand, setNewCommand] = useState("");
  const [newPiiMode, setNewPiiMode] = useState<PIIMode>("redact");
  const [newTimeout, setNewTimeout] = useState(60);
  const [newHeaders, setNewHeaders] = useState<SecretEdits>(NO_EDITS);
  const [newEnv, setNewEnv] = useState<SecretEdits>(NO_EDITS);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // Registered, but the connect failed: the server is kept, in "error".
  const [notice, setNotice] = useState<string | null>(null);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<MCPServerSummary | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

  // Scan dialog
  const [scanOpen, setScanOpen] = useState(false);
  const [scanHost, setScanHost] = useState("host.docker.internal");
  const [scanPortStart, setScanPortStart] = useState(8000);
  const [scanPortEnd, setScanPortEnd] = useState(9000);
  const [scanning, setScanning] = useState(false);
  const [scanResults, setScanResults] = useState<DiscoveredServer[]>([]);
  const [scanDone, setScanDone] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [registeringUrl, setRegisteringUrl] = useState<string | null>(null);

  function normalizeScanHost(rawHost: string): string {
    const trimmed = rawHost.trim();
    if (!trimmed) return "";

    const correctedTypos = trimmed.replace(
      /host\.docker\.internel/gi,
      "host.docker.internal",
    );

    const noScheme = correctedTypos.replace(/^https?:\/\//i, "");
    return noScheme.split("/")[0] ?? noScheme;
  }

  function openCreate() {
    setNewName("");
    setNewTransport("streamable_http");
    setNewPrefix("");
    setNewUrl("");
    setNewCommand("");
    // What the service would pick: Redact needs the PII service to run.
    setNewPiiMode(isModuleEnabled("pii") ? "redact" : "allow");
    setNewTimeout(60);
    setNewHeaders(NO_EDITS);
    setNewEnv(NO_EDITS);
    setCreateError(null);
    setCreateOpen(true);
  }

  async function handleCreate() {
    setCreating(true);
    setCreateError(null);
    setNotice(null);
    try {
      const server = await registerServer({
        name: newName,
        transport: newTransport,
        tool_prefix:
          newPrefix || newName.toLowerCase().replace(/[^a-z0-9]/g, "_"),
        ...(isHttp(newTransport)
          ? { url: newUrl.trim(), headers: toSecretMap(newHeaders) }
          : { command: splitCommand(newCommand), env: toSecretMap(newEnv) }),
        pii_mode: newPiiMode,
        timeout_sec: newTimeout,
        auto_discover: true,
      });
      setCreateOpen(false);
      if (server.warnings?.length) {
        setNotice(
          t("mcp.registered_not_connected", {
            name: server.name,
            reason: server.warnings.join(" "),
          }),
        );
      }
      await load();
    } catch (err: unknown) {
      // In the dialog, where it can be corrected: a name or prefix taken.
      setCreateError(
        err instanceof Error ? err.message : t("common.create_failed"),
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleRefresh(id: string) {
    try {
      await refreshServer(id);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.refresh_failed"));
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteServer(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.delete_failed"));
    } finally {
      setDeleting(false);
    }
  }

  function openScan() {
    setScanHost("host.docker.internal");
    setScanPortStart(8000);
    setScanPortEnd(9000);
    setScanResults([]);
    setScanDone(false);
    setScanError(null);
    setRegisteringUrl(null);
    setScanOpen(true);
  }

  async function handleScan() {
    const host = normalizeScanHost(scanHost);
    setScanHost(host);
    setScanning(true);
    setScanError(null);
    setScanResults([]);
    setScanDone(false);
    try {
      const result = await scanForServers(host, scanPortStart, scanPortEnd);
      setScanResults(result.discovered);
      setScanDone(true);
    } catch (err: unknown) {
      setScanError(
        err instanceof Error ? err.message : t("mcp.scan_failed", "Scan failed"),
      );
    } finally {
      setScanning(false);
    }
  }

  async function handleQuickRegister(server: DiscoveredServer) {
    setRegisteringUrl(server.url);
    setError(null);
    try {
      const prefix = server.server_name
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "_")
        .replace(/_+/g, "_")
        .replace(/^_|_$/g, "");
      const registered = await registerServer({
        name: server.server_name,
        transport: "streamable_http",
        tool_prefix: prefix || `mcp_${server.port}`,
        url: server.url,
        auto_discover: true,
      });
      if (registered.warnings?.length) {
        setNotice(
          t("mcp.registered_not_connected", {
            name: registered.name,
            reason: registered.warnings.join(" "),
          }),
        );
      }
      // Mark as registered in scan results
      setScanResults((prev) =>
        prev.map((s) =>
          s.url === server.url ? { ...s, already_registered: true } : s,
        ),
      );
      await load();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("common.create_failed"),
      );
    } finally {
      setRegisteringUrl(null);
    }
  }

  const columns: ColumnDef<MCPServerSummary>[] = [
    {
      key: "name",
      label: t("mcp.name", "Name"),
      sortable: true,
      sortValue: (s) => s.name,
      searchValue: (s) => `${s.name} ${s.tool_prefix}`,
      render: (s) => (
        <>
          <Typography fontWeight={600}>{s.name}</Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            fontFamily="monospace"
          >
            {s.tool_prefix}
          </Typography>
        </>
      ),
      minWidth: 200,
    },
    {
      key: "transport",
      label: t("mcp.transport", "Transport"),
      sortable: true,
      sortValue: (s) => s.transport,
      render: (s) => (
        <Chip
          size="small"
          label={s.transport.toUpperCase()}
          variant="outlined"
        />
      ),
      minWidth: 100,
    },
    {
      key: "status",
      label: t("mcp.status", "Status"),
      sortable: true,
      sortValue: (s) => s.status,
      render: (s) => (
        <Tooltip title={s.last_error ?? ""}>
          <Chip
            size="small"
            label={s.status}
            color={STATUS_COLOR[s.status] ?? "default"}
          />
        </Tooltip>
      ),
      minWidth: 120,
    },
    {
      key: "tools",
      label: t("mcp.tool_count", "Tools"),
      sortable: true,
      sortValue: (s) => s.discovered_tools,
      render: (s) => <Typography>{s.discovered_tools}</Typography>,
      minWidth: 80,
    },
    {
      key: "pii_mode",
      label: t("mcp.pii_mode", "PII Mode"),
      render: (s) => (
        <Chip size="small" label={s.pii_mode} variant="outlined" />
      ),
      minWidth: 100,
    },
    {
      key: "actions",
      label: t("common.actions", "Actions"),
      align: "right",
      render: (s) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          {s.has_settings && (
            <IconButton
              size="small"
              title={t("mcp.provider_settings", "Provider Settings")}
              onClick={() => navigate(`/admin/mcp/servers/${s.id}`)}
              color="primary"
            >
              <SettingsIcon fontSize="small" />
            </IconButton>
          )}
          <IconButton
            size="small"
            title={t("mcp.view_details", "View details")}
            onClick={() => navigate(`/admin/mcp/servers/${s.id}`)}
          >
            <OpenInNewIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            title={t("mcp.refresh", "Refresh")}
            onClick={() => handleRefresh(s.id)}
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            color="error"
            title={t("common.delete", "Delete")}
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
      {notice && (
        <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}

      <DataTable
        title={t("mcp.servers_title", "MCP Servers")}
        rows={servers}
        columns={columns}
        rowKey={(s) => s.id}
        loading={loading}
        onRefresh={load}
        emptyMessage={t("mcp.no_servers", "No MCP servers registered.")}
        searchPlaceholder={t("mcp.search_servers", "Search servers…")}
        toolbarActions={
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined"
              startIcon={<RadarIcon />}
              onClick={openScan}
              size="small"
            >
              {t("mcp.scan", "Scan Network")}
            </Button>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={openCreate}
              size="small"
            >
              {t("mcp.register_server", "Register Server")}
            </Button>
          </Stack>
        }
      />

      {/* ── Register server dialog ── */}
      <FormDialog
        open={createOpen}
        title={t("mcp.register_server", "Register Server")}
        loading={creating}
        submitLabel={t("mcp.register", "Register")}
        cancelLabel={t("common.cancel", "Cancel")}
        submitDisabled={
          !newName.trim() ||
          (isHttp(newTransport) ? !newUrl.trim() : !newCommand.trim())
        }
        onSubmit={handleCreate}
        onClose={() => setCreateOpen(false)}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          {createError && <Alert severity="error">{createError}</Alert>}
          <TextField
            label={t("mcp.server_name", "Server Name")}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            fullWidth
            required
            disabled={creating}
          />
          <TextField
            label={t("mcp.transport", "Transport")}
            select
            value={newTransport}
            onChange={(e) =>
              setNewTransport(e.target.value as MCPTransportType)
            }
            fullWidth
            disabled={creating}
          >
            <MenuItem value="streamable_http">
              {t("mcp.transport_streamable_http")}
            </MenuItem>
            <MenuItem value="sse">{t("mcp.transport_sse")}</MenuItem>
            <MenuItem value="stdio">{t("mcp.transport_stdio")}</MenuItem>
          </TextField>
          {isHttp(newTransport) && (
            <TextField
              label={t("mcp.server_url", "Server URL")}
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              placeholder={
                newTransport === "streamable_http"
                  ? "http://localhost:8100/mcp/"
                  : "http://localhost:3000/sse"
              }
              fullWidth
              required
              disabled={creating}
            />
          )}
          {newTransport === "stdio" && (
            <TextField
              label={t("mcp.command", "Command")}
              value={newCommand}
              onChange={(e) => setNewCommand(e.target.value)}
              placeholder="npx -y @modelcontextprotocol/server-brave-search"
              fullWidth
              required
              disabled={creating}
              helperText={t(
                "mcp.command_help",
                "Space-separated command and arguments",
              )}
            />
          )}
          <TextField
            label={t("mcp.tool_prefix", "Tool Prefix")}
            value={newPrefix}
            onChange={(e) => setNewPrefix(e.target.value)}
            placeholder={
              newName.toLowerCase().replace(/[^a-z0-9]/g, "_") || "my_server"
            }
            fullWidth
            disabled={creating}
            helperText={t(
              "mcp.prefix_help",
              "Unique prefix for tool names (e.g. 'brave'). Auto-generated if empty.",
            )}
          />
          <TextField
            label={t("mcp.pii_mode", "PII Mode")}
            select
            value={newPiiMode}
            onChange={(e) => setNewPiiMode(e.target.value as PIIMode)}
            fullWidth
            disabled={creating}
          >
            <MenuItem value="allow">{t("mcp.pii_allow")}</MenuItem>
            <MenuItem value="redact">{t("mcp.pii_redact")}</MenuItem>
            <MenuItem value="block">{t("mcp.pii_block")}</MenuItem>
          </TextField>
          <PiiServiceWarning mode={newPiiMode} />
          <TextField
            label={t("mcp.timeout_sec")}
            type="number"
            value={newTimeout}
            onChange={(e) => setNewTimeout(Number(e.target.value))}
            fullWidth
            disabled={creating}
            inputProps={{ min: 5, max: 600 }}
            helperText={t("mcp.timeout_help")}
          />
          {isHttp(newTransport) ? (
            <SecretPairsEditor
              label={t("mcp.headers")}
              help={t("mcp.headers_help")}
              edits={newHeaders}
              onChange={setNewHeaders}
              namePlaceholder="Authorization"
              valuePlaceholder="Bearer …"
              addLabel={t("mcp.add_header")}
            />
          ) : (
            <SecretPairsEditor
              label={t("mcp.env")}
              help={t("mcp.env_help")}
              edits={newEnv}
              onChange={setNewEnv}
              namePlaceholder="API_KEY"
              valuePlaceholder="…"
              addLabel={t("mcp.add_env")}
            />
          )}
        </Stack>
      </FormDialog>

      {/* ── Delete confirm ── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("mcp.delete_server", "Delete Server")}
        message={t("mcp.delete_confirm", { name: deleteTarget?.name ?? "" })}
        confirmLabel={t("common.delete", "Delete")}
        loading={deleting}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />

      {/* ── Scan dialog ── */}
      <Dialog
        open={scanOpen}
        onClose={() => !scanning && setScanOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{t("mcp.scan_title", "Scan for MCP Servers")}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label={t("mcp.scan_host", "Host / IP Address")}
              value={scanHost}
              onChange={(e) => setScanHost(e.target.value)}
              placeholder="host.docker.internal"
              fullWidth
              required
              disabled={scanning}
            />
            <Stack direction="row" spacing={2}>
              <TextField
                label={t("mcp.scan_port_start", "Port Start")}
                type="number"
                value={scanPortStart}
                onChange={(e) => setScanPortStart(Number(e.target.value))}
                disabled={scanning}
                fullWidth
                inputProps={{ min: 1, max: 65535 }}
              />
              <TextField
                label={t("mcp.scan_port_end", "Port End")}
                type="number"
                value={scanPortEnd}
                onChange={(e) => setScanPortEnd(Number(e.target.value))}
                disabled={scanning}
                fullWidth
                inputProps={{ min: 1, max: 65535 }}
              />
            </Stack>

            {scanning && (
              <Box>
                <LinearProgress />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  {t(
                    "mcp.scanning",
                    "Scanning ports {{start}}–{{end}} on {{host}}…",
                    {
                      start: scanPortStart,
                      end: scanPortEnd,
                      host: scanHost,
                    },
                  )}
                </Typography>
              </Box>
            )}

            {scanError && <Alert severity="error">{scanError}</Alert>}

            {scanDone && scanResults.length === 0 && (
              <Alert severity="info">
                {t(
                  "mcp.no_servers_found",
                  "No MCP servers found in the scanned port range.",
                )}
              </Alert>
            )}

            {scanResults.length > 0 && (
              <>
                <Divider />
                <Typography variant="subtitle2">
                  {t(
                    "mcp.discovered_servers",
                    "Discovered Servers ({{count}})",
                    {
                      count: scanResults.length,
                    },
                  )}
                </Typography>
                <List disablePadding>
                  {scanResults.map((s) => (
                    <ListItem
                      key={s.url}
                      secondaryAction={
                        s.already_registered ? (
                          <Tooltip
                            title={t(
                              "mcp.already_registered",
                              "Already registered",
                            )}
                          >
                            <CheckCircleIcon color="success" />
                          </Tooltip>
                        ) : (
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={() => handleQuickRegister(s)}
                            disabled={registeringUrl === s.url}
                            startIcon={
                              registeringUrl === s.url ? (
                                <CircularProgress size={16} />
                              ) : (
                                <AddIcon />
                              )
                            }
                          >
                            {t("mcp.register", "Register")}
                          </Button>
                        )
                      }
                      sx={{ pr: 16 }}
                    >
                      <ListItemText
                        primary={
                          <Stack
                            direction="row"
                            spacing={1}
                            alignItems="center"
                          >
                            <Typography fontWeight={600}>
                              {s.server_name}
                            </Typography>
                            <Chip
                              size="small"
                              label={`:${s.port}`}
                              variant="outlined"
                            />
                            {s.tools.length > 0 && (
                              <Chip
                                size="small"
                                label={t("mcp.tools_count", "{{count}} tools", {
                                  count: s.tools.length,
                                })}
                                color="primary"
                                variant="outlined"
                              />
                            )}
                          </Stack>
                        }
                        secondary={
                          <>
                            <Typography
                              variant="caption"
                              component="span"
                              fontFamily="monospace"
                            >
                              {s.url}
                            </Typography>
                            {s.tools.length > 0 && (
                              <Typography
                                variant="caption"
                                component="div"
                                color="text.secondary"
                                sx={{ mt: 0.5 }}
                              >
                                {s.tools.join(", ")}
                              </Typography>
                            )}
                          </>
                        }
                      />
                    </ListItem>
                  ))}
                </List>
              </>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setScanOpen(false)} disabled={scanning}>
            {t("common.close", "Close")}
          </Button>
          <Button
            variant="contained"
            onClick={handleScan}
            disabled={scanning || !scanHost.trim()}
            startIcon={
              scanning ? <CircularProgress size={16} /> : <RadarIcon />
            }
          >
            {scanning
              ? t("mcp.scanning_btn", "Scanning…")
              : t("mcp.scan", "Scan")}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
