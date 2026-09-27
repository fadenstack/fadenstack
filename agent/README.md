# faden_agent

`faden_agent` is the host-side execution bridge for Fadenstack node clusters.

It does not perform cluster scheduling. The backend remains authoritative for:

- desired state
- placement and routing policy
- GPU-aware filtering and load-balancing

The agent responsibilities are:

- enroll with one-time token
- maintain authenticated outbound stream
- execute node commands (Docker runtime lifecycle)
- report heartbeat, inventory, command timeline, and events

## Quick Start

1. Create token in backend admin:
   - `POST /api/admin/system/nodes/enrollment-tokens`
2. Set environment variables on node host:
   - `FADEN_NODE_AGENT_BACKEND_URL`
   - `FADEN_NODE_AGENT_ENROLLMENT_TOKEN`
   - `FADEN_NODE_AGENT_AGENT_ID`
   - `FADEN_NODE_AGENT_HOST`
3. Start service:
   - `faden-agent` (foreground, for testing)
   - `faden-agent start` (install as systemd service)

## Commands

| Command                | Description                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------- |
| `faden-agent`        | Run in foreground (test connectivity, Ctrl+C to stop)                              |
| `faden-agent start`  | Install and start as a systemd service (picks up `FADEN_NODE_AGENT_*` env vars) |
| `faden-agent stop`   | Stop and disable the systemd service                                               |
| `faden-agent status` | Show systemd service status                                                        |

## Environment Variables

- `FADEN_NODE_AGENT_BACKEND_URL` default `http://127.0.0.1:8000`
- `FADEN_NODE_AGENT_AGENT_ID` default `hostname`
- `FADEN_NODE_AGENT_HOST` default `hostname`
- `FADEN_NODE_AGENT_ADVERTISE_HOST` default `FADEN_NODE_AGENT_HOST`
- `FADEN_NODE_AGENT_ADVERTISE_SCHEME` default `http` (allowed: `http`, `https`)
- `FADEN_NODE_AGENT_ENROLLMENT_TOKEN` one-time token for initial enrollment
- `FADEN_NODE_AGENT_STATE_PATH` default `/var/lib/faden-agent/state.json`
- `FADEN_NODE_AGENT_HEARTBEAT_INTERVAL_SEC` default `15`
- `FADEN_NODE_AGENT_INVENTORY_INTERVAL_SEC` default `60`
- `FADEN_NODE_AGENT_RECONNECT_MIN_SEC` default `2`
- `FADEN_NODE_AGENT_RECONNECT_MAX_SEC` default `30`
- `FADEN_NODE_AGENT_REQUEST_TIMEOUT_SEC` default `20`
- `FADEN_NODE_AGENT_VERIFY_TLS` default `true`
- `FADEN_NODE_AGENT_LOKI_URL` Loki push endpoint (e.g. `http://10.0.0.1:3100`). When set, the agent collects system logs (journald on Linux, Event Log on Windows) and pushes them to Loki with labels `{job="node-agent", host="<hostname>", level="..."}`.
- `FADEN_NODE_AGENT_LOG_BATCH_SIZE` default `100` — max log lines per collection cycle
- `FADEN_NODE_AGENT_LOG_FLUSH_INTERVAL_SEC` default `5` — seconds between log collection cycles

## Binary Installation (no Python required)

Pre-built standalone binaries are available on the
[GitHub Releases](https://github.com/fadenstack/fadenstack/releases) page
for Linux (x86_64), Windows (x86_64), and macOS (universal).

### Linux

```bash
curl -fLO https://github.com/fadenstack/fadenstack/releases/download/node-agent-v0.2.0/faden-agent-linux-x86_64
sudo install -m 0755 faden-agent-linux-x86_64 /usr/local/bin/faden-agent
```

Then install the systemd service (see below) or run directly:

```bash
export FADEN_NODE_AGENT_BACKEND_URL=http://your-backend:8000
export FADEN_NODE_AGENT_ENROLLMENT_TOKEN=tok_xxx

# Test connectivity first (foreground, Ctrl+C to stop)
faden-agent

# Once working, install as a service
faden-agent start
```

### Windows

Download `faden-agent-windows-x86_64.exe` from the releases page and run:

```powershell
$env:FADEN_NODE_AGENT_BACKEND_URL = "http://your-backend:8000"
$env:FADEN_NODE_AGENT_ENROLLMENT_TOKEN = "tok_xxx"
.\faden-agent-windows-x86_64.exe
```

### macOS

```bash
curl -fLO https://github.com/fadenstack/fadenstack/releases/download/node-agent-v0.2.0/faden-agent-macos-universal
chmod +x faden-agent-macos-universal
export FADEN_NODE_AGENT_BACKEND_URL=http://your-backend:8000
export FADEN_NODE_AGENT_ENROLLMENT_TOKEN=tok_xxx
./faden-agent-macos-universal
```

### Via CLI

```bash
faden node agent deploy --backend-url http://your-backend:8000
```

The CLI auto-detects pre-built binaries in the workspace `dist/` directories and
uses them instead of cloning + setting up a Python venv.

## Systemd

A unit file template is available at:

- `deploy/systemd/faden-agent.service`
