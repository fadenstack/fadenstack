ARG BASE_IMAGE=ghcr.io/fadenstack/base:latest

# Node.js for npx-based MCP servers: the LTS from Node's own image. Debian bookworm's package is Node 18, out of
# support since April 2025, and current MCP servers need 20 or later (server-everything 2.0 stopped on its missing
# global crypto, 0.5.0 on the release server).
FROM node:24.22.0-bookworm-slim AS node

FROM ${BASE_IMAGE} AS prod

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --inexact --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --inexact --no-dev

# What the image is, as GHCR shows it (the same as .github/workflows/docker-publish.yml; cli/tests/test_image_labels.py).
LABEL org.opencontainers.image.title="Fadenstack MCP" \
      org.opencontainers.image.description="The Fadenstack MCP module: registers Model Context Protocol servers (stdio, SSE and streamable HTTP) and offers their tools to chats and agents. It can check each tool call's arguments with the PII module and mask or refuse personal data, as each server's setting says. Node.js is included for servers started with npx." \
      org.opencontainers.image.source="https://github.com/fadenstack/fadenstack" \
      org.opencontainers.image.licenses="Apache-2.0"

CMD ["/usr/local/bin/python", "-m", "faden_mcp"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups
