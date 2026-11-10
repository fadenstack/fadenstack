ARG BASE_IMAGE=ghcr.io/fadenstack/base:latest
FROM ${BASE_IMAGE} AS prod

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --inexact --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --inexact --no-dev

# LiteLLM uses the model price list it ships instead of fetching one from
# GitHub on every start.
ENV LITELLM_LOCAL_MODEL_COST_MAP=True

# What the image is, as GHCR shows it (the same as .github/workflows/docker-publish.yml; cli/tests/test_image_labels.py).
LABEL org.opencontainers.image.title="Fadenstack gateway" \
      org.opencontainers.image.description="The Fadenstack LLM gateway: the OpenAI-compatible API through which the console's chat, apps and agents reach the models. It routes each request to the provider or cluster that serves the model, applies the installation's policies on the way (personal data masked by the PII module, tool rules and approvals) and records usage." \
      org.opencontainers.image.source="https://github.com/fadenstack/fadenstack" \
      org.opencontainers.image.licenses="Apache-2.0"

CMD ["/usr/local/bin/python", "-m", "faden_gateway"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups
