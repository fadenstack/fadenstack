"""Deterministic compiler from a Fadenstack deployment spec to a Ray config.

Phase 3 turns an :class:`~faden_backend.services.inference.schemas.InferenceDeploymentSpecV1Alpha1`
plus the resolved :class:`~faden_backend.db.models.llm.LLMModel` record into the
``LLMServingArgs``-shaped dictionary that the head-node agent hands to
``ray.serve.llm.build_openai_app`` and deploys with ``serve.run``.

The compiler is **pure and Ray-free** on purpose: it imports nothing from
``ray`` and performs no I/O, so it can be unit-tested against golden fixtures
without a cluster, and it can never pull the (much larger, GPU-oriented) Ray
dependency into the backend image.  The only Ray knowledge it encodes are the
field names and value constraints of ``ray.serve.llm.LLMConfig`` /
``LLMServingArgs`` (verified against the pinned ``ray[serve]==2.58.0``), and a
whitelist of ``AcceleratorType`` values the backend can safely emit.

The mapping (see the Ray migration plan, Phase 3):

    engine.name         -> ``llm_engine`` ("vllm" -> "vLLM")
    engine.config       -> ``engine_kwargs``
    scale.replicas      -> ``deployment_config.num_replicas``
    scale.autoscale     -> ``deployment_config.autoscaling_config``
    resources.replica   -> ``placement_group_config.bundle_per_worker`` (only
                           for fractional GPU / explicit CPU; else Ray's
                           per-device default bundles)
    resources.placement,
    topology.nodes      -> ``placement_group_config.strategy``
    topology.*          -> ``engine_kwargs.tensor/pipeline_parallel_size``
    (accelerator)       -> ``accelerator_type``   (whitelisted, else omitted)
    (model/artifacts)   -> ``model_loading_config`` (HF id or local path)

Advanced features (P/D disaggregation, LoRA, prefix/KV-aware routing, custom
router replicas) are deliberately **not** emitted; they are left to Ray's
defaults and a later phase.

A picture model (``engine.name: diffusers``) compiles to the runtime image's
own document instead, ``{"image_configs": [...]}``: the helper serves it with
diffusers (``llm_port_ray_runtime/image.py``), not Serve LLM. Its
``engine.config`` holds the picture settings (``IMAGE_SETTINGS``), its GPU
share goes on the copy's actor, and it has no tensor or pipeline parallelism.

``resources.replica.machine`` pins every copy to one machine of the cluster:
the orchestrator resolves it to Ray's per-node resource (``node:<address>``)
and the compiler asks a sliver of it for each copy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from faden_backend.services.inference.schemas import (
    InferenceDeploymentSpecV1Alpha1,
    parse_inference_deployment_spec,
)

# Ray's ``ray.serve.llm`` only supports the vLLM engine.  The stored spec uses
# the short form (``"vllm"``); Ray's ``LLMConfig.llm_engine`` validator
# requires the exact ``"vLLM"`` spelling, and rejects everything else.
_RAY_ENGINE_VLLM = "vLLM"
#: Picture models run on diffusers in the runtime's image app.
IMAGE_ENGINE = "diffusers"
_SUPPORTED_ENGINE_NAMES = frozenset({"vllm", IMAGE_ENGINE})

#: What a picture model's ``engine.config`` may set: the image app's
#: ``pipeline_config``. Anything else would be a vLLM flag it cannot take.
IMAGE_SETTINGS = frozenset({"dtype", "num_inference_steps", "guidance_scale", "size", "max_pixels"})
_IMAGE_DTYPES = frozenset({"bfloat16", "float16", "float32"})
_IMAGE_SIZE = re.compile(r"^\d{3,4}x\d{3,4}$")

#: How much of Ray's per-node resource (``node:<address>``, 1.0 per node) a
#: pinned copy asks for: enough to bind it to the node, little enough for any
#: number of copies.
_NODE_PIN = 0.001

# Accelerators Ray's ``AcceleratorType`` registry (``ray[serve]==2.58.0``)
# actually accepts.  ``LLMConfig.accelerator_type`` raises
# "Unsupported accelerator type" for anything not on this list, so the
# compiler only emits a value it is certain Ray will accept.  Hardware Ray does
# not know (e.g. the DGX Spark's GB10) is intentionally left as ``None`` —
# vLLM schedules on plain ``GPU`` resources in that case, which is the correct
# behaviour for an unregistered accelerator.  ``A10`` is folded to ``A10G``
# (Ray's alias) so a bare "A10" spec still deploys.
_RAY_ACCELERATOR_WHITELIST: frozenset[str] = frozenset({
    "V100",
    "P100",
    "T4",
    "P4",
    "K80",
    "A10G",
    "L4",
    "L40S",
    "A100",
    "A100-40G",
    "A100-80G",
    "H100",
    "H200",
    "H20",
    "B200",
    "B300",
    "GB200",
    "GB300",
    "RTX-PRO-6000",
})
_ACCELERATOR_ALIASES: dict[str, str] = {
    "A10": "A10G",
    # Case-normalize common spellings without changing the family.
    "A100 40GB": "A100-40G",
    "A100 80GB": "A100-80G",
    "H100 80GB": "H100",
    "RTX PRO 6000": "RTX-PRO-6000",
}

# ``ModelLoadingConfig.model_id`` is the label clients hit in ``/v1/models``
# and use on every request.  It must be non-empty; we derive it from the
# record's ``display_name`` (sanitized) and fall back to the HF repo id.
_MODEL_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MODEL_ID_MAX = 128


class ArtifactResolutionError(ValueError):
    """Raised when the deployment's model artifact cannot be resolved.

    Subclasses ``ValueError`` so it is caught by the orchestrator's existing
    ``validate``/``plan`` error handling and surfaced as a deployment
    ``FAILED`` phase rather than an unhandled loop crash.
    """


class DeploymentValidationError(ValueError):
    """Raised when an InferenceDeploymentSpec is unsupported or invalid for Ray."""


def validate_spec(spec: InferenceDeploymentSpecV1Alpha1) -> None:
    """Validate that the spec is supported by the Ray driver.

    Raises:
        DeploymentValidationError: if any unsupported field or invalid combination is detected.
    """
    engine_name = (spec.engine.name or "").strip().lower()
    if engine_name not in _SUPPORTED_ENGINE_NAMES:
        raise DeploymentValidationError(
            f"unsupported engine for Ray: {spec.engine.name!r} (expected one of {sorted(_SUPPORTED_ENGINE_NAMES)})"
        )
    if engine_name == IMAGE_ENGINE:
        _validate_image(spec)
    if spec.replica_routing and spec.replica_routing.kv_aware:
        raise DeploymentValidationError(
            "KV-aware replica routing is not supported on Ray Serve v1alpha1"
        )
    if spec.scale.autoscale is not None:
        autoscale = spec.scale.autoscale
        min_rep = autoscale.min_replicas
        max_rep = autoscale.max_replicas
        if min_rep is not None and max_rep is not None and min_rep > max_rep:
            raise DeploymentValidationError(
                f"min_replicas ({min_rep}) cannot be greater than max_replicas ({max_rep})"
            )
        # Ray Serve autoscales on ongoing requests per replica, not on a
        # utilisation fraction; mapping one onto the other would scale to
        # max_replicas under any load.  Require Ray's own knob explicitly.
        if autoscale.target_utilization is not None or autoscale.metrics is not None:
            raise DeploymentValidationError(
                "scale.autoscale.target_utilization/metrics have no Ray Serve equivalent; "
                "use extensions.ray.target_ongoing_requests instead"
            )
    _ray_target_ongoing_requests(spec)  # validates the extension's type
    _placement(spec)  # validates the GPU / topology combination
    _extension_env_vars(spec)  # validates extension runtime_env keys


@dataclass(frozen=True)
class ArtifactResolution:
    """How the model weights reach the Ray worker.

    A resolved artifact is either a *local path* already present on the
    environment node(s) (``is_local``) or a *remote* (Hugging Face) id that the
    engine downloads on first worker start.
    """

    model_id: str
    model_source: str
    is_local: bool
    is_remote: bool


def _sanitize_model_id(raw: str | None, fallback: str | None) -> str:
    """Turn an arbitrary display name into a request-safe ``model_id``."""
    source = raw or fallback or None
    if not source:
        raise ArtifactResolutionError(
            "cannot derive a model_id: the LLMModel has neither display_name nor hf_repo_id"
        )
    cleaned = _MODEL_ID_SAFE.sub("-", source.strip()).strip("-.")
    cleaned = cleaned or "model"
    return cleaned[:_MODEL_ID_MAX]


def resolve_artifact(
    *,
    spec: InferenceDeploymentSpecV1Alpha1,
    model_display_name: str,
    model_source: str,
    hf_repo_id: str | None = None,
    hf_revision: str | None = None,
    availability_root_path: str | None = None,
) -> ArtifactResolution:
    """Resolve where the engine loads the weights from.

    Resolution order for ``model_source``:

    1. spec ``artifacts.source == "local_path"``  → the spec's ``root_path``;
    2. spec ``artifacts.source == "remote"``      → the spec's ``root_path``
       (a remote URI) when present, else the model's HF repo id;
    3. spec ``artifacts.source == "sync"`` (default) → the per-node
       ``ModelAvailability.root_path`` when the artifact is known-ready, else
       the model's HF repo id (the engine falls back to a download).

    The Hugging Face source prefers the record's ``hf_repo_id`` (the canonical
    remote identity) when the model is a HF model; otherwise the
    ``display_name`` acts as the repo id.  ``model_id`` is always a sanitized
    local label, independent of ``model_source``.

    Raises:
        ArtifactResolutionError: if a local-path source has no usable path.
    """
    artifacts = spec.artifacts
    source = (artifacts.source or "sync").strip().lower()

    if source == "local_path":
        path = artifacts.root_path
        if not path:
            raise ArtifactResolutionError(
                "artifacts.source='local_path' requires artifacts.root_path"
            )
        model_id = _sanitize_model_id(model_display_name, hf_repo_id)
        return ArtifactResolution(
            model_id=model_id,
            model_source=path,
            is_local=True,
            is_remote=False,
        )

    if source == "remote":
        path = artifacts.root_path
        repo = path or hf_repo_id or model_display_name
        model_id = _sanitize_model_id(model_display_name, hf_repo_id)
        return ArtifactResolution(
            model_id=model_id,
            model_source=repo,
            is_local=False,
            is_remote=True,
        )

    # source == "sync" (default): prefer a synced local root if we know the
    # artifact is present; otherwise let the engine pull from the HF repo.
    if availability_root_path:
        model_id = _sanitize_model_id(model_display_name, hf_repo_id)
        return ArtifactResolution(
            model_id=model_id,
            model_source=availability_root_path,
            is_local=True,
            is_remote=False,
        )
    repo = hf_repo_id or model_display_name
    model_id = _sanitize_model_id(model_display_name, hf_repo_id)
    return ArtifactResolution(
        model_id=model_id,
        model_source=repo,
        is_local=False,
        is_remote=True,
    )


def _accelerator_type(raw: str | None) -> str | None:
    """Map a spec accelerator name onto a Ray-accepted value, else ``None``.

    An unknown accelerator is *omitted* (``None``) rather than emitted, because
    ``LLMConfig`` would reject it and the deployment would fail to build.  A
    ``None`` accelerator is valid and lets vLLM schedule on generic ``GPU``
    resources.
    """
    if not raw:
        return None
    name = raw.strip()
    if not name:
        return None
    if name in _ACCELERATOR_ALIASES:
        name = _ACCELERATOR_ALIASES[name]
    if name in _RAY_ACCELERATOR_WHITELIST:
        return name
    return None


#: How replicas write their logs.
#:
#: Ray Serve's default is a human-shaped line: a timestamp, the deployment,
#: the replica id and the message, run together. Parsing labels back out of
#: that is a regex that breaks the first time Ray changes its formatter --
#: and it has to run on every line shipped.
#:
#: JSON encoding makes the same facts fields. The forwarder reads
#: ``deployment``, ``replica`` and ``levelname`` instead of inferring them,
#: and a message containing a newline or a colon stops being a parsing
#: hazard.
#:
#: ``logs_dir`` is deliberately left alone: Ray's own session layout is what
#: the session-directory mount and the log reader both address.
DEFAULT_LOGGING_CONFIG: dict[str, Any] = {
    "encoding": "JSON",
    # Access logs are one line per request per replica. On a busy cluster
    # that is the bulk of the volume and none of the signal -- the gateway
    # already records every request with its latency and token counts.
    "enable_access_log": False,
}


def _prefix_affinity_router() -> dict[str, Any]:
    """Send a chat to the copy that already holds its history in its KV cache.

    Serve's default router picks two copies at random and takes the less
    busy one, so with two copies every other turn of a conversation landed
    on a copy that had never seen it and recomputed the whole history --
    half the prefix-cache hits gone (chat plan, Phase 1). Ray's prefix-aware
    router follows the prompt's prefix while the copies' queues stay within
    ``imbalanced_threshold`` of each other; past that it balances as before.
    Its default threshold is infinite (never balance) and its prefix tree
    grows without end unless eviction is on, so both are set.

    Only when the deployment asks for it (``replica_routing.strategy:
    prefix_affinity``). It was every deployment's with more than one copy,
    and under 16 concurrent chats qwen-chat's ingress, where this router
    runs, stopped answering Serve's health checks: replaced three times in
    ten minutes, every request in it lost, 32 of 47 turns timed out (Ray
    2.58, DGX pair, 2026-09-26). Serve's default router took the same load
    the night before.
    """
    return {
        "request_router_class": "ray.serve.llm.request_router.PrefixCacheAffinityRouter",
        "request_router_kwargs": {"imbalanced_threshold": 10, "do_eviction": True},
    }


def _deployment_config(
    *,
    replicas: int,
    min_replicas: int | None,
    max_replicas: int | None,
    target_ongoing_requests: float | None = None,
    upscale_delay_s: float | None = None,
    downscale_delay_s: float | None = None,
) -> dict[str, Any]:
    """Build ``LLMConfig.deployment_config`` for a resolved replica count.

    * ACTIVE with fixed replicas → ``num_replicas``.
    * ACTIVE with autoscaling bounds → ``autoscaling_config`` (min/max).
      When ``autoscaling_config`` is emitted, ``num_replicas`` is NEVER emitted.
    * STOPPED → ``num_replicas: 0`` (scale to zero, no live workers).

    ``num_replicas`` and ``autoscaling_config`` are mutually exclusive in Ray's
    ``DeploymentConfig``; exactly one path is taken here.
    """
    # Structured logs on every path, including scale-to-zero: a deployment
    # that is being stopped is one whose logs are most likely to be read.
    config: dict[str, Any] = {"logging_config": dict(DEFAULT_LOGGING_CONFIG)}
    if replicas <= 0 and max_replicas is None:
        # Scale to zero with fixed replicas. Serve accepts num_replicas=0.
        config["num_replicas"] = 0
        return config

    if max_replicas is not None:
        autoscaling: dict[str, Any] = {
            "min_replicas": int(min_replicas if min_replicas is not None else 1),
            "max_replicas": int(max_replicas),
        }
        if target_ongoing_requests is not None:
            autoscaling["target_ongoing_requests"] = target_ongoing_requests
        if upscale_delay_s is not None:
            autoscaling["upscale_delay_s"] = float(upscale_delay_s)
        if downscale_delay_s is not None:
            autoscaling["downscale_delay_s"] = float(downscale_delay_s)
        config["autoscaling_config"] = autoscaling
    else:
        config["num_replicas"] = int(replicas)
    return config


#: Fraction of accelerator memory an engine may reserve when the spec does
#: not say.
#:
#: Ray Serve LLM defaults to 0.92, which is fine on a discrete GPU whose
#: memory is otherwise untouched.  On unified-memory hardware -- GB10, Grace
#: Hopper, Jetson -- that same number is measured against *system RAM* that
#: the operating system is already using, so it is simply unreachable:
#:
#:     ValueError: Free memory on device cuda:0 (109.83/121.69 GiB) on startup
#:     is less than desired GPU memory utilization (0.92, 111.95 GiB).
#:
#: The first replica on a freshly booted node squeaks in and the second does
#: not, which reads as "scaling does nothing" rather than as a memory
#: setting.  0.80 leaves roughly 24GB of headroom on a 122GB node, which is
#: comfortably more than the OS and the agent need, and an operator who wants
#: the last of it can still set ``engine.config.gpu_memory_utilization``.
DEFAULT_GPU_MEMORY_UTILIZATION = 0.80

#: Ask vLLM for its KV cache block metrics.
#:
#: Off by default in vLLM, which means three histograms are never declared:
#: how long a cache block lives before eviction, how long it sits idle first,
#: and the gap before a block is reused.  Those are the numbers that say
#: whether the cache is sized right for the traffic -- the difference between
#: "the model is slow" and "the model keeps recomputing the same prefixes".
#:
#: The cost is bounded by vLLM itself: ``kv_cache_metrics_sample`` defaults to
#: 0.01, so one block in a hundred is measured rather than every one.  An
#: operator who wants it off can still set ``engine.config.kv_cache_metrics``
#: to false, the same way every other default here can be overridden.
DEFAULT_KV_CACHE_METRICS = True


#: vLLM settings that take JSON.  vLLM's command line reads
#: ``--speculative-config '{"method": "mtp"}'`` into an object; Ray Serve hands
#: ``engine_kwargs`` to vLLM's Python side, which does not, and a setting kept
#: as JSON text -- what the deployment page's editor saves -- reached vLLM as a
#: string: "Failed to create vLLM engine config: 'str' object has no
#: attribute 'items'", nine times, before Serve gave up (.61, 2026-10-01).
#: The vLLM help marks each of these "Should either be a valid JSON string or
#: JSON keys passed individually".
JSON_ENGINE_SETTINGS = frozenset({
    "attention_config",
    "compilation_config",
    "default_chat_template_kwargs",
    "default_mm_loras",
    "diffusion_config",
    "ec_transfer_config",
    "fault_tolerance_config",
    "hf_overrides",
    "ir_op_priority",
    "kernel_config",
    "kv_events_config",
    "kv_transfer_config",
    "limit_mm_per_prompt",
    "media_io_kwargs",
    "mm_processor_kwargs",
    "model_class_overrides",
    "override_generation_config",
    "pooler_config",
    "quantization_config",
    "reasoning_config",
    "speculative_config",
    "structured_outputs_config",
    "weight_transfer_config",
})


def _json_settings(kwargs: dict[str, Any]) -> None:
    """Read JSON-valued settings kept as text into objects, in place.

    Text that is not JSON is refused here, before Serve tries the copy;
    empty text is the setting left unset.
    """
    for key in JSON_ENGINE_SETTINGS & kwargs.keys():
        value = kwargs[key]
        if not isinstance(value, str):
            continue
        if not value.strip():
            del kwargs[key]
            continue
        try:
            kwargs[key] = json.loads(value)
        except ValueError as exc:
            raise ValueError(f"engine setting {key} is not valid JSON ({exc})") from exc


def _engine_kwargs(
    *,
    engine_config: dict[str, Any],
    tensor_parallel_size: int | None,
    pipeline_parallel_size: int | None,
    revision: str | None = None,
    replica_gpus: float | None = None,
) -> dict[str, Any]:
    """Merge spec engine kwargs with topology-derived TP/PP and model revision.

    The operator-provided ``engine.config`` wins for any key it sets (an
    explicit override), but a topology TP/PP that the operator did *not* also
    place in ``engine.config`` is always injected, because vLLM reads TP/PP
    exclusively from ``engine_kwargs``.

    One exception: a copy that shares an accelerator (``replica_gpus`` below
    1) may use no more of its memory than its share.  vLLM's own default is
    0.9 and ours 0.80; either would crowd the other copies on the card, and so
    would a share the operator raised by hand.  The host form writes the same
    cap into the spec it creates; this is where it holds for a spec edited
    afterwards, from the deployment page or the API.
    """
    kwargs: dict[str, Any] = dict(engine_config or {})
    _json_settings(kwargs)
    if tensor_parallel_size is not None:
        kwargs.setdefault("tensor_parallel_size", int(tensor_parallel_size))
    if pipeline_parallel_size is not None:
        kwargs.setdefault("pipeline_parallel_size", int(pipeline_parallel_size))
    if revision:
        kwargs.setdefault("revision", str(revision))
    if replica_gpus is not None and 0 < replica_gpus < 1:
        share = round(float(replica_gpus), 2)
        asked = kwargs.get("gpu_memory_utilization")
        asked_number = isinstance(asked, (int, float)) and not isinstance(asked, bool) and asked > 0
        kwargs["gpu_memory_utilization"] = min(float(asked), share) if asked_number else share
    else:
        kwargs.setdefault("gpu_memory_utilization", DEFAULT_GPU_MEMORY_UTILIZATION)
    kwargs.setdefault("kv_cache_metrics", DEFAULT_KV_CACHE_METRICS)
    return kwargs


_PLACEMENT_STRATEGIES = frozenset({"PACK", "STRICT_PACK", "SPREAD", "STRICT_SPREAD"})

# Env vars a (user-controlled) spec may set on replicas.  Everything else in
# ``runtime_env`` (pip, working_dir, py_modules, ...) installs or runs code on
# the GPU nodes and is therefore not accepted from a spec.
# One entry per accelerator stack, because a ROCm or Level Zero node tunes
# through its own names -- an NVIDIA-only allow-list would silently reject
# every knob a non-NVIDIA node has.
_ENV_VAR_PREFIXES = (
    "VLLM_",
    "HF_",
    "NCCL_",   # NVIDIA collectives
    "CUDA_",
    "RCCL_",   # AMD collectives
    "HIP_",
    "ROCR_",
    "HSA_",
    "ZE_",     # Intel Level Zero
    "SYCL_",
    "DIFFUSERS_",  # picture models' attention backend and the like
)
# Each stack's "which devices can I see" override, denied for the same reason
# in every case: it would let a spec reach accelerators outside the allocation
# the scheduler gave it.  Extending the prefixes above without extending this
# set is what would turn a vendor addition into a hole.
_ENV_VAR_DENYLIST = frozenset(
    {
        "CUDA_VISIBLE_DEVICES",
        "HIP_VISIBLE_DEVICES",
        "ROCR_VISIBLE_DEVICES",
        "GPU_DEVICE_ORDINAL",
        "ZE_AFFINITY_MASK",
        "ONEAPI_DEVICE_SELECTOR",
        "SYCL_DEVICE_ALLOWLIST",
        "SYCL_DEVICE_FILTER",
    }
    | {
        # Where weights and code come from, and the credential for it. An
        # allowed prefix (HF_, VLLM_) is not an allowed setting: these point
        # the engine at another hub (HF_ENDPOINT, which also receives the
        # token), at a cache the spec fills (HF_MODULES_CACHE holds repository
        # code), at node files or plugins, or put a credential in the stored
        # spec. The server sets the ones it needs (HF_TOKEN) itself.
        "HF_ENDPOINT",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HF_XET_CACHE",
        "HF_ASSETS_CACHE",
        "HF_MODULES_CACHE",
        "HF_DATASETS_CACHE",
        "HF_TOKEN",
        "HF_TOKEN_PATH",
        "VLLM_ALLOWED_LOCAL_MEDIA_PATH",
        "VLLM_PLUGINS",
        "VLLM_LOGGING_CONFIG_PATH",
        "VLLM_TORCH_PROFILER_DIR",
        "VLLM_CACHE_ROOT",
        "VLLM_ASSETS_CACHE",
        "VLLM_CONFIG_ROOT",
        "VLLM_RPC_BASE_PATH",
    }
)


def _ray_extension(spec: InferenceDeploymentSpecV1Alpha1) -> dict[str, Any]:
    ext = (spec.extensions or {}).get("ray") if isinstance(spec.extensions, dict) else None
    return ext if isinstance(ext, dict) else {}


def _ray_target_ongoing_requests(spec: InferenceDeploymentSpecV1Alpha1) -> float | None:
    value = _ray_extension(spec).get("target_ongoing_requests")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise DeploymentValidationError("extensions.ray.target_ongoing_requests must be a positive number")
    return float(value)


def _extension_env_vars(spec: InferenceDeploymentSpecV1Alpha1) -> dict[str, str]:
    """Allow-listed env vars from the spec's extensions.

    Accepted sources: ``extensions.env_vars``, ``extensions.runtime_env.env_vars``
    and ``extensions.ray.runtime_env.env_vars``.

    Raises:
        DeploymentValidationError: for any other ``runtime_env`` key or an env
            var outside the allow-list.
    """
    exts = spec.extensions if isinstance(spec.extensions, dict) else {}
    merged: dict[str, Any] = {}
    if isinstance(exts.get("env_vars"), dict):
        merged.update(exts["env_vars"])
    for label, runtime_env in (
        ("extensions.runtime_env", exts.get("runtime_env")),
        ("extensions.ray.runtime_env", _ray_extension(spec).get("runtime_env")),
    ):
        if not isinstance(runtime_env, dict):
            continue
        for key, value in runtime_env.items():
            if key != "env_vars":
                raise DeploymentValidationError(
                    f"{label}.{key} is not accepted from a deployment spec; only env_vars may be set"
                )
            if isinstance(value, dict):
                merged.update(value)
    for key in merged:
        if key in _ENV_VAR_DENYLIST or not str(key).startswith(_ENV_VAR_PREFIXES):
            raise DeploymentValidationError(
                f"env var {key!r} is not allowed (allowed prefixes: {', '.join(_ENV_VAR_PREFIXES)}; "
                f"never {', '.join(sorted(_ENV_VAR_DENYLIST))})"
            )
    return {str(k): str(v) for k, v in merged.items()}


def is_image_spec(spec: InferenceDeploymentSpecV1Alpha1) -> bool:
    """Whether the spec is for a picture model (``engine.name: diffusers``)."""
    return (spec.engine.name or "").strip().lower() == IMAGE_ENGINE


def _validate_image(spec: InferenceDeploymentSpecV1Alpha1) -> None:
    """A picture model runs on one device, with picture settings only."""
    topology = spec.topology
    if int(topology.tensor_parallel_size or 1) != 1 or int(topology.pipeline_parallel_size or 1) != 1:
        raise DeploymentValidationError("a picture model runs on one device: no tensor or pipeline parallelism")
    unknown = sorted(set(spec.engine.config or {}) - IMAGE_SETTINGS)
    if unknown:
        raise DeploymentValidationError(
            f"engine.config {', '.join(unknown)} is not a picture setting "
            f"(a diffusers model takes {', '.join(sorted(IMAGE_SETTINGS))})"
        )
    config = spec.engine.config or {}
    dtype = config.get("dtype")
    if dtype is not None and str(dtype) not in _IMAGE_DTYPES:
        raise DeploymentValidationError(f"engine.config.dtype must be one of {sorted(_IMAGE_DTYPES)}")
    size = config.get("size")
    if size is not None and not _IMAGE_SIZE.match(str(size)):
        raise DeploymentValidationError("engine.config.size must be WIDTHxHEIGHT, such as 1024x1024")
    for key in ("num_inference_steps", "max_pixels"):
        value = config.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise DeploymentValidationError(f"engine.config.{key} must be a positive whole number")
    guidance = config.get("guidance_scale")
    number = isinstance(guidance, (int, float)) and not isinstance(guidance, bool)
    if guidance is not None and (not number or guidance < 0):
        raise DeploymentValidationError("engine.config.guidance_scale must be a number of 0 or more")
    gpus = spec.resources.replica.gpus
    if gpus is not None and not 0 < float(gpus) <= 1:
        raise DeploymentValidationError("resources.replica.gpus of a picture model is a share of one device (0 to 1)")


def _placement(spec: InferenceDeploymentSpecV1Alpha1, node_resource: str | None = None) -> dict[str, Any] | None:
    """``placement_group_config`` for the replica, or ``None`` for Ray's default.

    Ray LLM's default is one ``{"GPU": 1}`` bundle per device (TP×PP) with
    strategy ``PACK`` (cross-node, best effort) — the right shape for
    integral GPUs, including TP/PP spread over several 1-GPU nodes.  A config
    is emitted only to change that: a fractional GPU per worker, an explicit
    CPU request, a placement strategy, or a ``topology.nodes`` pin (which maps
    to a strategy by the section-15 rule lock).  It always uses ``bundle_per_worker``
    (Ray expands it to TP×PP bundles; a config without bundles would produce
    *no* bundles), and never a hand-built ``accelerator_type:X`` key — Ray adds
    its own fractional accelerator hint from ``LLMConfig.accelerator_type``.

    Raises:
        DeploymentValidationError: GPU count inconsistent with TP×PP, or an
            unknown/impossible placement request.
    """
    topology = spec.topology
    num_devices = int(topology.tensor_parallel_size or 1) * int(topology.pipeline_parallel_size or 1)
    replica = spec.resources.replica
    gpus = float(replica.gpus) if replica.gpus is not None else None

    per_worker_gpu = 1.0
    if gpus is not None and gpus != num_devices:
        if num_devices == 1 and 0 < gpus < 1:
            per_worker_gpu = gpus  # fractional single-device replica
        else:
            raise DeploymentValidationError(
                f"resources.replica.gpus ({replica.gpus}) must equal tensor_parallel_size x "
                f"pipeline_parallel_size ({num_devices}), or be a fraction when that is 1"
            )

    strategy: str | None = None
    if spec.resources.placement:
        strategy = spec.resources.placement.strip().upper()
        if strategy not in _PLACEMENT_STRATEGIES:
            raise DeploymentValidationError(
                f"resources.placement {spec.resources.placement!r} is not one of {sorted(_PLACEMENT_STRATEGIES)}"
            )
    else:
        # Check extensions.ray for placementStrategy override
        ray_ext = _ray_extension(spec)
        ext_placement = ray_ext.get("placementStrategy") or ray_ext.get("placement_strategy")
        if ext_placement and isinstance(ext_placement, str):
            strategy = ext_placement.strip().upper()
            if strategy not in _PLACEMENT_STRATEGIES:
                raise DeploymentValidationError(
                    f"extensions.ray.placementStrategy {ext_placement!r} is not one of {sorted(_PLACEMENT_STRATEGIES)}"
                )

    if topology.nodes is not None:
        if topology.nodes > num_devices:
            raise DeploymentValidationError(
                f"topology.nodes ({topology.nodes}) exceeds the replica's devices ({num_devices})"
            )
        if strategy == "STRICT_PACK" and topology.nodes > 1:
            raise DeploymentValidationError(
                f"STRICT_PACK is impossible with topology.nodes ({topology.nodes}) > 1; "
                "STRICT_PACK forces all bundles onto a single node and causes deadlock"
            )
        if strategy is None:
            # Rule lock (Phase3_upgrade.md section 15, evidence-driven on the
            # DGX Spark pair): nodes == 1 colocates strictly, nodes > 1 spreads.
            # Without this, ``topology.nodes`` is a no-op and Ray's default soft
            # PACK can spill a TP group across hosts for nodes: 1 (silent
            # tensor parallelism over the network) or pack nodes: 2 onto one
            # host.  An explicit operator strategy still wins — it is validated
            # against the deadlock case just above.
            strategy = "STRICT_PACK" if topology.nodes == 1 else "SPREAD"

    if per_worker_gpu == 1.0 and replica.cpu is None and strategy is None and node_resource is None:
        return None
    bundle: dict[str, float] = {"GPU": per_worker_gpu}
    if replica.cpu is not None:
        bundle["CPU"] = float(replica.cpu) / num_devices
    if node_resource is not None:
        # Every worker of every copy on the pinned machine.
        bundle[node_resource] = _NODE_PIN
    config: dict[str, Any] = {"bundle_per_worker": bundle}
    if strategy is not None:
        config["strategy"] = strategy
    return config


def compile_spec(
    *,
    spec: InferenceDeploymentSpecV1Alpha1,
    artifact: ArtifactResolution,
    desired_state: str = "active",
    accelerator_type: str | None = None,
    revision: str | None = None,
    runtime_env: dict[str, Any] | None = None,
    node_resource: str | None = None,
) -> dict[str, Any]:
    """Compile one resolved deployment into a Ray ``LLMServingArgs`` document.

    Args:
        spec: the validated deployment spec.
        artifact: the resolved artifact (``model_id`` / ``model_source``).
        desired_state: ``"active"`` or ``"stopped"`` (affects replica count).
        accelerator_type: resolved accelerator name *before* the Ray whitelist
            mapping (the caller passes the raw spec value; this function maps).
        revision: optional model revision to thread into engine_kwargs.
        runtime_env: optional runtime_env dictionary to inject into LLMConfig.
        node_resource: Ray's resource for the machine every copy is pinned to
            (``node:<address>``), resolved by the orchestrator from
            ``resources.replica.machine``.

    Returns:
        A plain dictionary shaped like ``ray.serve.llm.LLMServingArgs`` with a
        single ``llm_configs`` entry.  It carries only JSON-safe values and no
        Ray types, so it can be embedded in a control-plane command payload.
    """
    validate_spec(spec)
    if is_image_spec(spec):
        return _compile_image(
            spec=spec, artifact=artifact, desired_state=desired_state,
            runtime_env=runtime_env, node_resource=node_resource,
        )

    topology = spec.topology
    tp = topology.tensor_parallel_size
    pp = topology.pipeline_parallel_size

    effective_revision = spec.artifacts.revision or revision
    replica_gpus = spec.resources.replica.gpus
    engine_kwargs = _engine_kwargs(
        engine_config=spec.engine.config,
        tensor_parallel_size=tp,
        pipeline_parallel_size=pp,
        revision=effective_revision,
        replica_gpus=float(replica_gpus) if replica_gpus is not None else None,
    )

    # Determine the active replica representation.
    stopped = (desired_state or "active").strip().lower() == "stopped"
    scale = spec.scale
    if scale.autoscale is not None:
        replicas = int(scale.autoscale.min_replicas)
        min_replicas = int(scale.autoscale.min_replicas)
        max_replicas = int(scale.autoscale.max_replicas)
    else:
        replicas = int(scale.replicas if scale.replicas is not None else 1)
        min_replicas = None
        max_replicas = None

    deployment_config = _deployment_config(
        replicas=0 if stopped else replicas,
        min_replicas=None if stopped else min_replicas,
        max_replicas=None if stopped else max_replicas,
        target_ongoing_requests=_ray_target_ongoing_requests(spec),
        upscale_delay_s=(scale.autoscale.scale_up_timeout if scale.autoscale else None),
        downscale_delay_s=(scale.autoscale.scale_down_timeout if scale.autoscale else None),
    )
    prefix_affinity = spec.replica_routing is not None and spec.replica_routing.strategy == "prefix_affinity"
    if prefix_affinity and not stopped and max(replicas, max_replicas or 0) > 1:
        deployment_config["request_router_config"] = _prefix_affinity_router()

    effective_accelerator = _accelerator_type(accelerator_type)

    placement_group_config = None if stopped else _placement(spec, node_resource)

    model_loading_config: dict[str, Any] = {
        "model_id": artifact.model_id,
        "model_source": artifact.model_source,
    }

    llm_config: dict[str, Any] = {
        "llm_engine": _RAY_ENGINE_VLLM,
        "model_loading_config": model_loading_config,
        "engine_kwargs": engine_kwargs,
        "deployment_config": deployment_config,
    }

    # runtime_env: the spec (user-controlled) may only contribute allow-listed
    # env vars; ``runtime_env`` passed by the backend itself is trusted.
    effective_runtime_env: dict[str, Any] = dict(runtime_env or {})
    spec_env_vars = _extension_env_vars(spec)
    if spec_env_vars:
        env_vars = dict(effective_runtime_env.get("env_vars") or {})
        env_vars.update(spec_env_vars)
        effective_runtime_env["env_vars"] = env_vars
    fraction = spec.resources.replica.gpus
    if fraction is not None and 0 < float(fraction) < 1:
        # A copy sharing an accelerator: its placement group reserves the
        # share, but vLLM's Ray executor asks for a whole GPU per worker
        # unless told otherwise, and never fits the bundle -- found on the DGX
        # pair as "resource request {'GPU': 1.0} cannot fit into any bundles
        # ... [{'CPU': 1.0, 'GPU': 0.1}]". Last, so a spec cannot contradict it.
        env_vars = dict(effective_runtime_env.get("env_vars") or {})
        env_vars["VLLM_RAY_PER_WORKER_GPUS"] = str(round(float(fraction), 2))
        effective_runtime_env["env_vars"] = env_vars
    if effective_runtime_env:
        llm_config["runtime_env"] = effective_runtime_env

    # accelerator_type only when Ray will accept it (None is a valid omission).
    if effective_accelerator is not None:
        llm_config["accelerator_type"] = effective_accelerator
    if placement_group_config is not None:
        llm_config["placement_group_config"] = placement_group_config

    return {
        "llm_configs": [llm_config],
        "ingress_cls_config": {},  # default: ray.serve.llm.OpenAiIngress
        # The ingress is a deployment too, and its logging is configured
        # separately from the engines'. Without this its lines stayed
        # human-formatted while the engines' became JSON, so half the
        # deployment's log panel had a parsed level and a clean message and
        # half did not -- from the same deployment, in the same view.
        "ingress_deployment_config": {
            "logging_config": dict(DEFAULT_LOGGING_CONFIG),
        },
    }


def _replica_counts(spec: InferenceDeploymentSpecV1Alpha1) -> tuple[int, int | None, int | None]:
    """(replicas, min, max) from ``scale``: a fixed count, or autoscaling bounds."""
    scale = spec.scale
    if scale.autoscale is not None:
        return (
            int(scale.autoscale.min_replicas),
            int(scale.autoscale.min_replicas),
            int(scale.autoscale.max_replicas),
        )
    return int(scale.replicas if scale.replicas is not None else 1), None, None


def _compile_image(
    *,
    spec: InferenceDeploymentSpecV1Alpha1,
    artifact: ArtifactResolution,
    desired_state: str,
    runtime_env: dict[str, Any] | None,
    node_resource: str | None,
) -> dict[str, Any]:
    """The runtime image's document for a picture model (``image_configs``).

    One deployment, ``ImageServer:<model>``, that is also the application's
    ingress. Its GPU share is the copy's own (default one device): Ray
    reserves it on the actor, as it reserves a vLLM copy's in its placement
    group.
    """
    stopped = (desired_state or "active").strip().lower() == "stopped"
    replicas, min_replicas, max_replicas = _replica_counts(spec)
    scale = spec.scale
    deployment_config = _deployment_config(
        replicas=0 if stopped else replicas,
        min_replicas=None if stopped else min_replicas,
        max_replicas=None if stopped else max_replicas,
        target_ongoing_requests=_ray_target_ongoing_requests(spec),
        upscale_delay_s=(scale.autoscale.scale_up_timeout if scale.autoscale else None),
        downscale_delay_s=(scale.autoscale.scale_down_timeout if scale.autoscale else None),
    )
    replica = spec.resources.replica
    actor: dict[str, Any] = {"num_gpus": float(replica.gpus) if replica.gpus is not None else 1.0}
    if replica.cpu is not None:
        actor["num_cpus"] = float(replica.cpu)
    if node_resource is not None:
        actor["resources"] = {node_resource: _NODE_PIN}
    deployment_config["ray_actor_options"] = actor

    settings = dict(spec.engine.config or {})
    settings.setdefault("dtype", "bfloat16")
    config: dict[str, Any] = {
        "model_loading_config": {"model_id": artifact.model_id, "model_source": artifact.model_source},
        "pipeline_config": settings,
        "deployment_config": deployment_config,
    }
    effective_runtime_env: dict[str, Any] = dict(runtime_env or {})
    spec_env_vars = _extension_env_vars(spec)
    if spec_env_vars:
        env_vars = dict(effective_runtime_env.get("env_vars") or {})
        env_vars.update(spec_env_vars)
        effective_runtime_env["env_vars"] = env_vars
    if effective_runtime_env:
        config["runtime_env"] = effective_runtime_env
    return {"image_configs": [config]}


def compile_deployment(
    *,
    spec_data: dict[str, Any],
    model_display_name: str,
    model_source: str,
    hf_repo_id: str | None = None,
    hf_revision: str | None = None,
    availability_root_path: str | None = None,
    capabilities: dict[str, Any] | None = None,
    desired_state: str = "active",
    runtime_env: dict[str, Any] | None = None,
    node_resource: str | None = None,
) -> dict[str, Any]:
    """End-to-end, testable compiler entrypoint.

    Validates *spec_data* through :func:`parse_inference_deployment_spec`,
    resolves the artifact, and returns the ``LLMServingArgs`` document.  This is
    the single function the orchestrator calls — and the one golden-fixture
    tests target.  ``capabilities`` is accepted for API stability (gating
    advanced features is Phase 4+); the deployment accelerator (if any) is read
    from the spec's ``resources.replica.accelerator``.
    """
    spec = parse_inference_deployment_spec(spec_data)
    effective_revision = spec.artifacts.revision or hf_revision
    artifact = resolve_artifact(
        spec=spec,
        model_display_name=model_display_name,
        model_source=model_source,
        hf_repo_id=hf_repo_id,
        hf_revision=effective_revision,
        availability_root_path=availability_root_path,
    )
    accelerator = (spec.resources.replica.accelerator or None)
    return compile_spec(
        spec=spec,
        artifact=artifact,
        desired_state=desired_state,
        accelerator_type=accelerator,
        revision=effective_revision,
        runtime_env=runtime_env,
        node_resource=node_resource,
    )


# Convenience re-export for callers that only need the whitelist.
__all__ = [
    "ArtifactResolution",
    "ArtifactResolutionError",
    "DeploymentValidationError",
    "_RAY_ACCELERATOR_WHITELIST",
    "compile_deployment",
    "compile_spec",
    "resolve_artifact",
    "validate_spec",
]
