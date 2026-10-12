# ── Fadenstack platform base image ──────────────────────────────────
#
# Pre-installs Python, uv, gcc, and the ~27 common packages shared
# across all Fadenstack microservices.
#
# Service Dockerfiles extend this with:
#   ARG BASE_IMAGE=ghcr.io/fadenstack/base:latest
#   FROM ${BASE_IMAGE} AS prod
# ──────────────────────────────────────────────────────────────────

ARG UV_VERSION=0.9.12
ARG PYTHON_BASE=python:3.13-slim-bookworm

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ${PYTHON_BASE}

# gcc is required by several services for native C extensions
# (asyncpg, cryptography, pydantic-core, uvloop, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Bake uv into the image so downstream Dockerfiles don't need the
# --mount=from=uv dance. Its image holds only the binary, so its licences
# (MIT OR Apache-2.0, at UV_VERSION) come from licenses/ here.
COPY --from=uv /uv /bin/uv
COPY licenses/uv-LICENSE-MIT licenses/uv-LICENSE-APACHE /usr/share/licenses/uv/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_MANAGED_PYTHON=1

# loguru prints the values of local variables in tracebacks by default (prompts,
# provider keys, tokens). The services set diagnose=False on their sinks; this is
# the second guard for any sink added later.
ENV LOGURU_DIAGNOSE=NO

WORKDIR /app/src

# Pre-install common platform packages: exact versions, every file checked against the sha256
# hashes in requirements.txt (made from requirements.in by relock.py, which keeps the versions
# the services' uv.lock files pin). Each service's `uv sync --locked` still adjusts to its own
# lock file, but most packages are already at the right version, so the per-service layer is small.
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --compile-bytecode --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
