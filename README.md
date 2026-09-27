# Fadenstack

> Self-hosted all-in-one LLM platform — gateway, chat console, control plane, and optional modules in a single release.

This monorepo contains the **core** components of [Fadenstack](https://fadenstack.github.io).
Fadenstack was called LLM.Port up to release 0.3; see [Moving from LLM.Port](#moving-from-llmport).

## Components

| Directory        | Description                              | Runtime                    |
| ---------------- | ---------------------------------------- | -------------------------- |
| `backend`        | Control plane API (FastAPI)              | Docker                     |
| `frontend`       | React admin console                      | Docker                     |
| `gateway`        | OpenAI-compatible gateway                | Docker                     |
| `pii`            | PII detection & redaction (Presidio)     | Docker (profile: `pii`)    |
| `mcp`            | MCP tool registry                        | Docker (profile: `mcp`)    |
| `skills`         | Skills registry                          | Docker (profile: `skills`) |
| `shared`         | Compose files, nginx, base image, initdb | Docker                     |
| `cli`            | CLI installer & management tool (`faden`) | PyPI / pipx               |
| `agent`          | Machine agent (`faden-agent`)            | Standalone binary          |
| `runtime-image`  | The certified Ray + vLLM runtime image   | Docker                     |

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
| The server | [Containers](docs/containers.md) · [MCP servers](docs/mcp-servers.md) · [HTTPS and trust](docs/https-and-trust.md) |
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

A release `v0.4.0` publishes them as `0.4.0`, `0.4` and `latest`. The CLI of a
release runs the images of its own version, so `fadenstack 0.4.0` deploys
`0.4.0`.

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
3. For a new machine agent: set its version in `agent/faden_agent/__init__.py`,
   `agent/pyproject.toml` and `AGENT_VERSION` in the backend, then tag and push
   `node-agent-v<version>`. That publishes the Linux x86_64 and aarch64 binaries
   the install line fetches.

## Machine agent

The agent is distributed as standalone binaries (no Python required).
Machines get it through the install line on the console's **Machines → Add a
machine** page. See [agent/README.md](agent/README.md)
and the [Releases](https://github.com/fadenstack/fadenstack/releases) page.

## Moving from LLM.Port

Release 0.4.0 renamed everything an install carries: the compose project and
its volumes, the containers, the databases, the environment variables
(`LLM_PORT_*` became `FADEN_*`, the gateway's `LLM_PORT_API_*` became
`FADEN_GATEWAY_*`), the CLI (`llmport` became `faden`) and the agent
(`llmport-agent` became `faden-agent`). An install of LLM.Port 0.3 moves
across with the migration tool, which keeps every row, file and setting:
[llm-port/migrate-to-fadenstack](https://github.com/llm-port/migrate-to-fadenstack).

## Licence

What runs as part of Fadenstack is licensed under the
[GNU Affero General Public License v3.0 only](LICENSE) (`AGPL-3.0-only`): the
backend, the gateway, the console, the PII, MCP and skills services, the
compose files, the machine agent and the runtime image. The licence carries an
additional permission for combining Fadenstack with NVIDIA's CUDA libraries;
see [NOTICE](NOTICE).

What you build on or wrap in your own tooling is licensed under the
[Apache License 2.0](cli/LICENSE): the CLI (`cli/`), the SDKs and the MCP
servers.

Releases up to and including 0.3 were published as LLM.Port under the Apache
License 2.0, and remain available under it.
