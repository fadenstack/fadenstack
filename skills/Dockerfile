ARG BASE_IMAGE=ghcr.io/fadenstack/base:latest
FROM ${BASE_IMAGE} AS prod

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --inexact --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --inexact --no-dev

# What the image is, as GHCR shows it (the same as .github/workflows/docker-publish.yml; cli/tests/test_image_labels.py).
LABEL org.opencontainers.image.title="Fadenstack skills" \
      org.opencontainers.image.description="The Fadenstack skills module: stores skills, reusable instruction packs in the Agent Skills format, with their owners, sharing and fixed versions, and decides which skills a chat request receives." \
      org.opencontainers.image.source="https://github.com/fadenstack/fadenstack" \
      org.opencontainers.image.licenses="Apache-2.0"

CMD ["/usr/local/bin/python", "-m", "faden_skills"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups
