# Fadenstack

[![PyPI version](https://img.shields.io/pypi/v/fadenstack)](https://pypi.org/project/fadenstack/)
[![Python 3.12+](https://img.shields.io/pypi/pyversions/fadenstack)](https://pypi.org/project/fadenstack/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/fadenstack/fadenstack/blob/main/cli/LICENSE)

The single entry point to **deploy, configure, and manage** the
[fadenstack](https://fadenstack.com) platform — an open-source LLM gateway
that routes, secures, and observes traffic across local runtimes and remote
providers.

```
pipx install fadenstack       # or: uv tool install fadenstack
faden deploy                 # installs Fadenstack into ~/fadenstack and starts it
```

The CLI carries the deployment files of its release and runs that release's
published images: `fadenstack 0.4.3` deploys Fadenstack 0.4.3. To upgrade,
upgrade the CLI and run `faden upgrade`. See
[Installing Fadenstack](https://github.com/fadenstack/fadenstack/blob/main/docs/help/pages/operate/installing.md).

---

## Features

- **One-command production deploy** — pre-flight checks, `.env` generation,
  the release's images, database migrations, and service startup.
- **Upgrade, backup and restore** — `faden upgrade` backs up every database,
  moves the install to the CLI's release and keeps your settings.
- **Auto-tuning** — detects host CPU / RAM and computes optimal worker counts,
  DB pool sizes, and queue channel pools.
- **Module management** — enable / disable optional services
  (PII redaction, MCP tools, skills, provenance) via Docker Compose profiles.
- **GPU detection** — `faden doctor` lists the host's GPUs; `faden deploy`
  adds the NVIDIA GPU overlay when the host has an NVIDIA runtime.
- **Developer workflow** — clone all repos, install deps, run infra + migrations,
  and generate a VS Code workspace in one command.
- **Rich terminal UI** — tables, progress bars, and themed output powered by
  [Rich](https://github.com/Textualize/rich).
- **Command abbreviation** — type `faden st` instead of `faden status`.

---

## Requirements

| Dependency     | Minimum |
| -------------- | ------- |
| Python         | 3.12    |
| Docker Engine  | 24.0    |
| Docker Compose | v2      |

Git is only needed to deploy from a source checkout (`--build`) or for the
developer workflow.

---

## Installation

### From PyPI

```bash
pip install fadenstack

# or with uv
uv tool install fadenstack
```

### From source

```bash
git clone https://github.com/fadenstack/fadenstack.git
cd fadenstack/cli
uv sync                   # install deps + editable entry point
uv run faden --help
```

### Verify

```bash
faden version
faden doctor
```

---

## Quick start

### Production deployment

```bash
# Full deploy — pre-flight, env gen, pull this release's images, migrate, start
faden deploy                      # into ~/fadenstack
faden deploy /opt/faden         # into a directory of your choice

# Enable optional modules
faden deploy --modules pii,mcp

# From a source checkout: build the images instead of pulling them
faden deploy --build

# Upgrade to the CLI's release (backs up first), back up, restore
faden upgrade
faden backup
faden restore ~/fadenstack/backups/<time>

# Provision node-agent on this host during deploy
faden deploy /opt/faden --local-node

# Provision node-agent on remote host over SSH
faden deploy /opt/faden --local-node --local-node-host ubuntu@10.0.0.12
```

### Day-to-day operations

```bash
faden up                          # start all services
faden up --build faden-gateway     # rebuild a single service
faden down                        # stop all services
faden down --volumes              # stop + remove volumes
faden status                      # service table
faden logs -f                     # tail all logs
faden logs -f faden-backend    # tail one service
```

### Modules

```bash
faden module list                 # show available modules
faden module enable pii skills    # enable modules
faden module disable mcp          # disable a module
```

Provenance marks AI-generated content (EU AI Act Art. 50(2)) and is on by
default: `faden deploy` and `faden upgrade` switch it on. `faden module disable
provenance` warns that generated content will no longer be marked, asks first
(`--yes` for scripts), and records in faden.yaml who switched it off and when;
upgrades then leave it off until `faden module enable provenance`.

### Configuration

```bash
faden config show                 # print current config
faden config set dev.branch main  # update a value (dot-notation)
faden config edit                 # open in $EDITOR
faden config path                 # print config file location
```

### Auto-tuning

```bash
faden tune                        # detect resources, write .env
faden tune --profile prod         # production-grade sizing
faden tune --dry-run              # preview without writing
```

### Admin utilities

```bash
faden admin reset-password --email admin@localhost
```

### Developer workflow

```bash
# Bootstrap everything — clone repos, install deps, start infra,
# run migrations, generate VS Code workspace
faden dev init ~/projects/fadenstack
faden dev init ~/projects/fadenstack --ssh        # SSH cloning
faden dev init ~/projects/fadenstack --overwrite   # re-clone existing repos

# Start / stop / status
faden dev up                            # on this machine only (127.0.0.1)
faden dev up --lan                      # on every interface, for one run
faden dev up --backend-only
faden dev up --modules pii,mcp,skills,provenance   # also run the optional modules
faden dev up --local-node
faden dev up --local-node --local-node-host ubuntu@10.0.0.12
faden dev down
faden dev status

# Check dev prerequisites (optionally auto-install missing tools)
faden dev doctor
faden dev doctor --install --yes
```

`--modules` runs PII (8003), MCP (8007), skills (8008) and provenance (8009)
on the host beside the backend, and points the gateway and backend at them.
Provenance has a repository of its own: it runs from a checkout beside the
core one (`../fadenstack-provenance`), and without that checkout `dev up`
says so and starts the rest. Without `--modules`, the
modules switched on with `faden module enable` run; the others are switched
off in the gateway and backend. See
[What happens to a request](../docs/help/pages/administer/requests.md).

`--local-node` installs a systemd unit by default and requires sudo on Linux.
Use `--local-node-no-sudo` to skip privileged systemd setup.

---

## Command reference

| Command                                      | Description                                                   |
| -------------------------------------------- | ------------------------------------------------------------- |
| `faden version`                            | Print CLI, Python, Docker, and Compose versions               |
| `faden doctor`                             | Run system health checks (OS, RAM, disk, Docker, GPU, ports)  |
| `faden deploy [DIR]`                       | Full production deployment with pre-flight checks             |
| `faden upgrade`                            | Back up, move to the CLI's release, restart with health gate  |
| `faden backup` / `faden restore DIR`     | Dump every database and `.env` / put them back                |
| `faden maintenance on\|off\|status`       | Show the maintenance page instead of the platform (`-m` note) |
| `faden deploy [DIR] --local-node`          | Deploy + provision node-agent (local or SSH host)            |
| `faden up [SERVICES...]`                   | Start services (supports `--build`, `--pull`)                 |
| `faden down`                               | Stop and remove containers (`--volumes`, `--all`)             |
| `faden status`                             | Show service state, health, and ports (`--json`)              |
| `faden logs [SERVICES...]`                 | Stream logs (`-f`, `-n`, `--timestamps`)                      |
| `faden config show\|set\|edit\|path\|init` | Manage YAML configuration                                     |
| `faden module list\|enable\|disable`       | Toggle optional platform modules                              |
| `faden tune`                               | Auto-tune worker and pool settings (`--profile`, `--dry-run`) |
| `faden admin reset-password`               | Reset a user password directly in the database                |
| `faden dev init [DIR]`                     | Bootstrap full developer workspace                            |
| `faden dev up`                             | Start backend, worker, gateway, and frontend dev servers      |
| `faden dev up --lan`                       | The same, reachable from the network (dev mode signs anyone in as admin) |
| `faden dev up --modules pii,mcp,skills`    | Also run the optional modules on the host                     |
| `faden dev up --local-node`                | Start dev services and provision node-agent                   |
| `faden dev down`                           | Stop all dev processes                                        |
| `faden dev status`                         | Show repo branches, infra, and dev processes                  |
| `faden dev doctor`                         | Check dev prerequisites (`--install`)                         |

---

## Project structure

```
cli/
├── pyproject.toml
├── src/
│   └── faden_cli/
│       ├── cli.py                 # Click root group + AliasedGroup
│       ├── commands/
│       │   ├── version.py         # version
│       │   ├── doctor.py          # doctor
│       │   ├── deploy.py          # deploy
│       │   ├── up.py              # up
│       │   ├── down.py            # down
│       │   ├── status.py          # status
│       │   ├── logs_cmd.py        # logs
│       │   ├── config.py          # config group
│       │   ├── module.py          # module group
│       │   ├── tune.py            # tune
│       │   ├── admin.py           # admin group
│       │   └── dev/               # developer workflow
│       │       ├── dev_init.py
│       │       ├── dev_up.py
│       │       ├── dev_status.py
│       │       └── dev_doctor.py
│       ├── core/                  # shared utilities
│       │   ├── compose.py         # Docker Compose wrapper
│       │   ├── detect.py          # OS / GPU / port detection
│       │   ├── settings.py        # YAML config (~/.config/faden/)
│       │   ├── api_client.py      # Backend REST client (httpx)
│       │   ├── bootstrap.py       # First-admin bootstrap
│       │   ├── env_gen.py         # .env generation (Jinja2)
│       │   ├── git.py             # Git clone / checkout helpers
│       │   ├── install.py         # Cross-platform tool installer
│       │   ├── registry.py        # Central metadata registry
│       │   ├── sysinfo.py         # Resource detection + tuning
│       │   └── console.py         # Rich themed output
│       ├── tui/                   # Textual TUI wizard
│       └── templates/             # Jinja2 .env templates
└── tests/
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

Part of the [fadenstack](https://github.com/fadenstack) project.
