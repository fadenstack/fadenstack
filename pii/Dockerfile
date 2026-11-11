ARG BASE_IMAGE=ghcr.io/fadenstack/base:latest
FROM ${BASE_IMAGE} AS prod

RUN --mount=type=cache,target=/root/.cache/uv \
  --mount=type=bind,source=uv.lock,target=uv.lock \
  --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
  uv sync --locked --inexact --no-install-project --no-dev

# Download spaCy English model for Presidio NER.
# Install via pip (uv pip) so the model lands in UV_PROJECT_ENVIRONMENT
# and benefits from the Docker layer cache, avoiding `spacy download`
# which shells out to pip in a way that can bypass the uv environment.
RUN uv pip install --system \
  https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --inexact --no-dev

# What the image is, as GHCR shows it (the same as .github/workflows/docker-publish.yml; cli/tests/test_image_labels.py).
LABEL org.opencontainers.image.title="Fadenstack PII" \
  org.opencontainers.image.description="The Fadenstack PII module: finds personal data in text, such as names, e-mail addresses, phone numbers and places, so that the gateway can mask it before a request reaches a model. This image detects with Microsoft Presidio and spaCy's English model." \
  org.opencontainers.image.source="https://github.com/fadenstack/fadenstack" \
  org.opencontainers.image.licenses="Apache-2.0"

CMD ["/usr/local/bin/python", "-m", "faden_pii"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --all-groups

# Explicit offline neural images. CPU never installs the CUDA extra.
FROM ${BASE_IMAGE} AS neural-base
WORKDIR /app/src
COPY pyproject.toml uv.lock ./
RUN uv pip install --system \
  https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/de_core_news_sm-3.8.0/de_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/fr_core_news_sm-3.8.0/fr_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/es_core_news_sm-3.8.0/es_core_news_sm-3.8.0-py3-none-any.whl
COPY . .
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
CMD ["/usr/local/bin/python", "-m", "faden_pii"]

FROM neural-base AS prod-cpu
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --inexact --no-dev --extra onnx
ENV FADEN_PII_BATCH_SHORT=8 FADEN_PII_BATCH_LONG=2
LABEL org.opencontainers.image.title="Fadenstack PII (CPU)" \
  org.opencontainers.image.description="The Fadenstack PII module, neural variant for CPUs: finds personal data in text, such as names, e-mail addresses, phone numbers and places, so that the gateway can mask it before a request reaches a model. It detects with a GLiNER model on ONNX Runtime, with spaCy models for English, German, French and Spanish." \
  org.opencontainers.image.source="https://github.com/fadenstack/fadenstack" \
  org.opencontainers.image.licenses="Apache-2.0"

FROM neural-base AS prod-gpu
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --inexact --no-dev --extra gliner2
LABEL org.opencontainers.image.title="Fadenstack PII (GPU)" \
  org.opencontainers.image.description="The Fadenstack PII module, neural variant for NVIDIA GPUs: finds personal data in text, such as names, e-mail addresses, phone numbers and places, so that the gateway can mask it before a request reaches a model. It detects with GLiNER2 on PyTorch, with spaCy models for English, German, French and Spanish." \
  org.opencontainers.image.source="https://github.com/fadenstack/fadenstack" \
  org.opencontainers.image.licenses="Apache-2.0"
