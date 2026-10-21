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

CMD ["/usr/local/bin/python", "-m", "faden_mcp"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups
