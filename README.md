# llm-port-core

> Self-hosted all-in-one LLM platform — gateway, chat console, control plane, and optional modules in a single release.

This monorepo contains all **core** (Apache 2.0) components of [llm.port](https://llm-port.github.io).

## Components

| Directory             | Description                              | Runtime                    |
| --------------------- | ---------------------------------------- | -------------------------- |
| `llm_port_backend`    | Control plane API (FastAPI)              | Docker                     |
| `llm_port_frontend`   | React admin console                      | Docker                     |
| `llm_port_api`        | OpenAI-compatible gateway                | Docker                     |
| `llm_port_pii`        | PII detection & redaction (Presidio)     | Docker (profile: `pii`)    |
| `llm_port_mcp`        | MCP tool registry                        | Docker (profile: `mcp`)    |
| `llm_port_skills`     | Skills registry                          | Docker (profile: `skills`) |
| `llm_port_shared`     | Compose files, nginx, base image, initdb | Docker                     |
| `llm_port_cli`        | CLI installer & management tool          | PyPI / pipx                |
| `llm_port_node_agent` | Remote node execution agent              | Standalone binary          |

## Quick Start

On a Linux (x86_64) server with Docker:

```bash
pipx install llmport-cli        # or: uv tool install llmport-cli
llmport deploy
```

Then open `http://<server>`, sign in, and add the machines with GPUs from
**Machines → Add a machine**. See [Installing LLM.Port](docs/installing.md),
and [Upgrading LLM.Port](docs/upgrading.md) for upgrades.

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

- `ghcr.io/llm-port/backend`
- `ghcr.io/llm-port/api`
- `ghcr.io/llm-port/frontend`
- `ghcr.io/llm-port/pii`
- `ghcr.io/llm-port/mcp`
- `ghcr.io/llm-port/skills`

A release `v0.3.0` publishes them as `0.3.0`, `0.3` and `latest`. The CLI of a
release runs the images of its own version, so `llmport-cli 0.3.0` deploys
`0.3.0`.

## Building from source

```bash
git clone https://github.com/llm-port/llm-port-core.git
cd llm-port-core
pipx install ./llm_port_cli
llmport deploy --build
```

## Releasing

1. Set the version in `llm_port_cli/pyproject.toml` and
   `llm_port_cli/src/llmport/__init__.py`.
2. Tag and push `v<version>`. That builds the images of that version, then
   publishes the CLI to PyPI once they exist.
3. For a new node agent: set its version in `llm_port_node_agent` and
   `AGENT_VERSION` in the backend, then tag and push `node-agent-v<version>`.
   That publishes the Linux x86_64 and aarch64 binaries the install line
   fetches.

## Node Agent

The node agent is distributed as standalone binaries (no Python required).
Machines get it through the install line on the console's **Machines → Add a
machine** page. See [llm_port_node_agent/README.md](llm_port_node_agent/README.md)
and the [Releases](https://github.com/llm-port/llm-port-core/releases) page.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
