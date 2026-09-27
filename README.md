# fadenstack

> Self-hosted all-in-one LLM platform — gateway, chat console, control plane, and optional modules in a single release.

This monorepo contains all **core** (Apache 2.0) components of [fadenstack](https://fadenstack.github.io).

## Components

| Directory             | Description                              | Runtime                    |
| --------------------- | ---------------------------------------- | -------------------------- |
| `faden_backend`    | Control plane API (FastAPI)              | Docker                     |
| `frontend`   | React admin console                      | Docker                     |
| `faden_gateway`        | OpenAI-compatible gateway                | Docker                     |
| `faden_pii`        | PII detection & redaction (Presidio)     | Docker (profile: `pii`)    |
| `faden_mcp`        | MCP tool registry                        | Docker (profile: `mcp`)    |
| `faden_skills`     | Skills registry                          | Docker (profile: `skills`) |
| `shared`     | Compose files, nginx, base image, initdb | Docker                     |
| `cli`        | CLI installer & management tool          | PyPI / pipx                |
| `faden_agent` | Remote node execution agent              | Standalone binary          |

## Quick Start

On a Linux (x86_64) server with Docker:

```bash
pipx install fadenstack        # or: uv tool install fadenstack
faden deploy
```

Then open `http://<server>`, sign in, and add the machines with GPUs from
**Machines → Add a machine**. See [Installing Fadenstack](docs/installing.md),
and [Upgrading Fadenstack](docs/upgrading.md) for upgrades.

## Documentation

| | |
| --- | --- |
| Setting up | [Installing](docs/installing.md) · [Upgrading](docs/upgrading.md) · [System requirements](docs/system-requirements.md) |
| Using it | [The chat](docs/chat.md) · [Skills](docs/skills.md) · [The admin dashboard](docs/dashboard.md) |
| GPU machines and models | [Adding a machine](docs/onboarding-a-node.md) · [Hosting a model from the marketplace](docs/model-marketplace.md) · [vLLM you already run](docs/found-vllm.md) · [Taking over clusters](docs/taking-over-clusters.md) · [When something fails](docs/resilience.md) · [FAQ](docs/faq.md) |
| The server | [Containers](docs/containers.md) · [MCP servers](docs/mcp-servers.md) |
| How it works | [The gateway's chat pipeline](docs/gateway-pipeline.md) · [RAG Lite search](docs/rag-lite-search.md) |
| Maintainers | [Releasing](docs/releasing.md) · [Onboarding friction](docs/onboarding-friction.md) |

## Docker Images

The images are published to the GitHub Container Registry:

- `ghcr.io/fadenstack/backend`
- `ghcr.io/fadenstack/gateway`
- `ghcr.io/fadenstack/frontend`
- `ghcr.io/fadenstack/pii`
- `ghcr.io/fadenstack/mcp`
- `ghcr.io/fadenstack/skills`

A release `v0.3.0` publishes them as `0.3.0`, `0.3` and `latest`. The CLI of a
release runs the images of its own version, so `fadenstack 0.3.0` deploys
`0.3.0`.

## Building from source

```bash
git clone https://github.com/fadenstack/fadenstack.git
cd fadenstack
pipx install ./cli
faden deploy --build
```

## Releasing

1. Set the version in `cli/pyproject.toml` and
   `cli/src/faden_cli/__init__.py`.
2. Tag and push `v<version>`. That builds the images of that version, then
   publishes the CLI to PyPI once they exist.
3. For a new node agent: set its version in `faden_agent` and
   `AGENT_VERSION` in the backend, then tag and push `node-agent-v<version>`.
   That publishes the Linux x86_64 and aarch64 binaries the install line
   fetches.

## Node Agent

The node agent is distributed as standalone binaries (no Python required).
Machines get it through the install line on the console's **Machines → Add a
machine** page. See [agent/README.md](agent/README.md)
and the [Releases](https://github.com/fadenstack/fadenstack/releases) page.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
