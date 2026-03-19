import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { nodesApi, type ManagedNode, type NodeCommand, type NodeCommandTimeline } from "~/api/nodes";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

function statusColor(status: string): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (status === "maintenance" || status === "draining" || status === "degraded") return "warning";
  if (status === "offline" || status === "error") return "error";
  return "default";
}

export default function NodeDetailPage() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const [node, setNode] = useState<ManagedNode | null>(null);
  const [commands, setCommands] = useState<NodeCommand[]>([]);
  const [timeline, setTimeline] = useState<NodeCommandTimeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sortedCommands = useMemo(
    () =>
      [...commands].sort(
        (a, b) => Date.parse(b.issued_at) - Date.parse(a.issued_at),
      ),
    [commands],
  );

  async function load() {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const [nodeRes, commandRes] = await Promise.all([
        nodesApi.get(id),
        nodesApi.listCommands(id),
      ]);
      setNode(nodeRes);
      setCommands(commandRes);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load node.");
    } finally {
      setLoading(false);
    }
  }

  async function runAction(key: string, action: () => Promise<void>) {
    setBusyKey(key);
    try {
      await action();
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function loadTimeline(commandId: string) {
    if (!id) return;
    setBusyKey(`timeline:${commandId}`);
    try {
      setTimeline(await nodesApi.commandTimeline(id, commandId));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load timeline.");
    } finally {
      setBusyKey(null);
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (error && !node) {
    return <Alert severity="error">{error}</Alert>;
  }

  if (!node || !id) {
    return <Alert severity="error">Node not found.</Alert>;
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Box>
          <Typography variant="h5">{node.host}</Typography>
          <Typography variant="caption" color="text.secondary" fontFamily="monospace">
            {node.id}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button variant="outlined" onClick={() => navigate("/admin/nodes")}>
            Back To Fleet
          </Button>
          <Chip size="small" color={statusColor(node.status)} label={node.status} />
        </Stack>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      <Card variant="outlined">
        <CardContent>
          <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems="flex-start">
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2">Agent</Typography>
              <Typography variant="body2" fontFamily="monospace">{node.agent_id}</Typography>
              <Typography variant="subtitle2" sx={{ mt: 1 }}>Version</Typography>
              <Typography variant="body2">{node.version ?? "-"}</Typography>
              <Typography variant="subtitle2" sx={{ mt: 1 }}>Last Seen</Typography>
              <Typography variant="body2">
                {node.last_seen ? new Date(node.last_seen).toLocaleString() : "-"}
              </Typography>
            </Box>
            <Divider orientation="vertical" flexItem sx={{ display: { xs: "none", md: "block" } }} />
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Node Controls</Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                <Button
                  size="small"
                  variant="outlined"
                  disabled={busyKey === "maintenance"}
                  onClick={() =>
                    runAction("maintenance", async () => {
                      await nodesApi.setMaintenance(id, !node.maintenance_mode);
                    })
                  }
                >
                  {node.maintenance_mode ? "Disable Maintenance" : "Enable Maintenance"}
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={busyKey === "drain"}
                  onClick={() =>
                    runAction("drain", async () => {
                      await nodesApi.setDrain(id, !node.draining);
                    })
                  }
                >
                  {node.draining ? "Disable Drain" : "Enable Drain"}
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={busyKey === "refresh_inventory"}
                  onClick={() =>
                    runAction("refresh_inventory", async () => {
                      await nodesApi.issueCommand(id, { command_type: "refresh_inventory" });
                    })
                  }
                >
                  Refresh Inventory
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={busyKey === "collect_diagnostics"}
                  onClick={() =>
                    runAction("collect_diagnostics", async () => {
                      await nodesApi.issueCommand(id, { command_type: "collect_diagnostics" });
                    })
                  }
                >
                  Collect Diagnostics
                </Button>
              </Stack>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Inventory</Typography>
          <Typography component="pre" sx={{ m: 0, fontSize: "0.78rem", fontFamily: "monospace", whiteSpace: "pre-wrap" }}>
            {JSON.stringify(node.latest_inventory ?? {}, null, 2)}
          </Typography>
          <Typography variant="subtitle2" sx={{ mt: 2, mb: 1 }}>Utilization</Typography>
          <Typography component="pre" sx={{ m: 0, fontSize: "0.78rem", fontFamily: "monospace", whiteSpace: "pre-wrap" }}>
            {JSON.stringify(node.latest_utilization ?? {}, null, 2)}
          </Typography>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Commands</Typography>
          {sortedCommands.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No commands yet.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {sortedCommands.map((command) => (
                <Box
                  key={command.id}
                  sx={{
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 1,
                    px: 1.5,
                    py: 1,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 1,
                  }}
                >
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600}>
                      {command.command_type}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {new Date(command.issued_at).toLocaleString()} | {command.status}
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    variant="text"
                    disabled={busyKey === `timeline:${command.id}`}
                    onClick={() => loadTimeline(command.id)}
                  >
                    Timeline
                  </Button>
                </Box>
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>

      {timeline && (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Timeline: {timeline.command.command_type}
            </Typography>
            <Stack spacing={1}>
              {timeline.events.map((event) => (
                <Box key={`${event.seq}:${event.ts}`} sx={{ borderLeft: 2, borderColor: "divider", pl: 1.5 }}>
                  <Typography variant="body2" fontWeight={600}>
                    #{event.seq} {event.phase}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {new Date(event.ts).toLocaleString()}
                  </Typography>
                  <Typography variant="body2">{event.message}</Typography>
                </Box>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}
    </Box>
  );
}
