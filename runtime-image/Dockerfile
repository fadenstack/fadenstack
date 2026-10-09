FROM nvcr.io/nvidia/vllm:26.09-py3

# Air-gapped runtime defaults and telemetry suppression
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    VLLM_DO_NOT_TRACK=1 \
    RAY_DISABLE_USAGE_STATS=1 \
    DO_NOT_TRACK=1

# Install Ray 2.58.0, pyarrow, and dashboard/metrics agent dependencies
# (preserving NVIDIA-optimized vLLM, PyTorch, and CUDA stack).
#
# Nothing NVIDIA ships may move: pip resolves our additions against the base's
# own versions (constraints). Without them the first 26.09 build pulled
# opentelemetry-sdk 1.45 over the base's 1.44 for the Prometheus exporter.
# What `pip check` already says about the base is kept, so the check below
# holds our layer to adding nothing to it.
WORKDIR /opt/llm-port/build
RUN pip list --format=freeze > base-packages.txt && \
    { pip check > base-pip-check.txt 2>&1 || true; }
RUN pip install --no-cache-dir --no-deps ray==2.58.0 pyarrow && \
    pip install --no-cache-dir -c base-packages.txt aiohttp-cors opencensus opentelemetry-exporter-prometheus
RUN { pip check > pip-check.txt 2>&1 || true; } && \
    if grep -vxFf base-pip-check.txt pip-check.txt | grep -v '^No broken requirements found'; then \
        echo "our layer added the conflicts above"; exit 1; \
    fi

# Ray 2.58 imports two vLLM modules from where vLLM 0.29 no longer has them
# (vllm_compat.py, temporary); then every vLLM module and name Ray Serve LLM
# imports must resolve, or the build fails here instead of on a node.
COPY vllm_compat.py check_ray_vllm.py /opt/llm-port/build/
RUN python3 vllm_compat.py && python3 check_ray_vllm.py

# Fail the build if the per-node metrics path cannot load.
#
# Worker nodes export Prometheus metrics through Ray's dashboard agent, whose
# `reporter_agent` imports `opencensus` at module scope (reporter_agent.py:15)
# and pulls in `ray._private.prometheus_exporter`, which needs it too.  When
# that import fails the agent never binds --metrics-export-port, so the head
# looks healthy while every worker stays silent -- which is exactly how the
# image shipped to the DGX pair: `opencensus`, `aiohttp-cors` and
# `opentelemetry-exporter-prometheus` were all absent from the deployed image
# even though the line above installs them, and nothing caught it until the
# two-node certification's worker scrape timed out.  Importing the real
# modules here is the only check that would have.
RUN python3 -c "import aiohttp_cors, opencensus; \
import ray._private.prometheus_exporter as pe; \
import ray.dashboard.modules.reporter.reporter_agent as ra; \
print('metrics stack OK:', opencensus.__name__, aiohttp_cors.__name__, pe.__name__, ra.__name__)"

# Picture models (text to image) run on diffusers beside vLLM: the helper's
# image app (llm_port_ray_runtime/image.py). Held to the base's versions like
# the layer above -- torch, Transformers and the Hub client stay NVIDIA's.
ARG DIFFUSERS_VERSION=0.41.0
ARG ACCELERATE_VERSION=1.15.0
RUN pip install --no-cache-dir -c base-packages.txt \
        "diffusers==${DIFFUSERS_VERSION}" "accelerate==${ACCELERATE_VERSION}" && \
    { pip check > pip-check.txt 2>&1 || true; } && \
    if grep -vxFf base-pip-check.txt pip-check.txt | grep -v '^No broken requirements found'; then \
        echo "diffusers added the conflicts above"; exit 1; \
    fi

# Install LLM.Port Ray Runtime helper package and validation assets
WORKDIR /opt/llm-port/runtime
COPY setup.py /opt/llm-port/runtime/
COPY LICENSE NOTICE /opt/llm-port/runtime/
COPY llm_port_ray_runtime /opt/llm-port/runtime/llm_port_ray_runtime/
COPY fixtures.json test_local_validation.py /opt/llm-port/runtime/

RUN pip install --no-cache-dir /opt/llm-port/runtime && \
    llm-port-ray-runtime versions && \
    python3 -c "from llm_port_ray_runtime.image import build_image_app, diffusers_without_xformers as d; \
import accelerate; p = d().ZImagePipeline; \
print('image app OK:', d().__version__, accelerate.__version__, p.__name__)"

# The helper is the host/runtime control contract (4B): the Node Agent invokes
# these verbs over `exec` and never imports Ray itself.  A build whose helper
# lacks a verb would leave the agent unable to deploy or tear down a Serve
# application, so the surface is asserted at build time rather than discovered
# on a node at deploy time.
RUN for verb in versions probe cluster-status serve-status run-serve-app scale-serve-app delete-serve-app; do \
        llm-port-ray-runtime "$verb" --help > /dev/null 2>&1 || \
            { echo "helper is missing verb: $verb"; exit 1; }; \
    done && echo 'helper control contract OK'

WORKDIR /workspace

CMD ["llm-port-ray-runtime", "versions"]

