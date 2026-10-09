"""Whether a cluster's runtime can load a model at all, said before anything is downloaded.

nvidia/Qwen3.8-Flash-Next-NVFP4 -- 132.7 GB -- was downloaded to both DGX
Sparks, and only then did vLLM say that its ``model_type`` (``qwen4_exp``,
saved with Transformers 5.16.0.dev0) was one the runtime's Transformers
5.14.1 did not know. The model's ``config.json`` said so from the start, and
the marketplace reads it anyway for the memory estimate.

Each runtime image lists what it can load (``runtime-image`` manifests,
read off the image by ``rebuild_runtime_image.py``): the model types its
Transformers and vLLM can parse a config of, and the architectures vLLM runs
natively. The verdicts:

``cannot_run``
    No part of the runtime can read the model's config. vLLM stops there,
    whatever the hardware. Hosting is refused.
``may_not_run``
    It can read it, but vLLM has no implementation of the architecture: it
    can only try its generic Transformers backend, which not every model
    supports. Or, for a runtime whose lists are not known, the model was
    saved with a newer Transformers than the runtime has.
``runs`` / ``unknown``
    Known to both; or nothing to go on (no config, or the repository brings
    its own code).

A picture model (a diffusers ``pipeline``) runs in the runtime's image app:
it runs when the runtime's diffusers has the pipeline, and cannot when the
runtime has no image app or a diffusers without it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CANNOT_RUN = "cannot_run"
MAY_NOT_RUN = "may_not_run"
RUNS = "runs"
UNKNOWN = "unknown"


@dataclass
class RuntimeSupport:
    """What one cluster's runtime can load."""

    vllm_version: str | None = None
    transformers_version: str | None = None
    model_types: frozenset[str] = field(default_factory=frozenset)
    architectures: frozenset[str] = field(default_factory=frozenset)
    #: The picture pipelines its image app runs; empty: none (or not known).
    pipelines: frozenset[str] = field(default_factory=frozenset)
    diffusers_version: str | None = None


@dataclass
class Compat:
    """A model on one cluster's runtime: the verdict, and why, in the operator's terms."""

    status: str
    #: ``model_type_unknown``, ``architecture_not_native``, ``newer_transformers``, ``remote_code``,
    #: ``no_config``, ``no_image_app``, ``pipeline_unknown``.
    code: str | None = None
    message: str | None = None
    model_type: str | None = None
    architectures: list[str] = field(default_factory=list)
    model_transformers_version: str | None = None
    vllm_version: str | None = None
    transformers_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _newer(a: str | None, b: str | None) -> bool:
    """Whether version *a* is later than *b* (both known)."""
    if not a or not b:
        return False
    try:
        from packaging.version import Version  # noqa: PLC0415

        return Version(a) > Version(b)
    except Exception:  # noqa: BLE001 - an unreadable version says nothing
        return False


def check(facts: dict[str, Any] | None, runtime: RuntimeSupport | None) -> Compat:
    """Whether a runtime can load the model whose ``config.json`` facts are *facts*."""
    facts = facts or {}
    model_type = facts.get("model_type")
    architectures = [a for a in facts.get("architectures") or [] if a]
    saved_with = facts.get("transformers_version")
    runtime = runtime or RuntimeSupport()
    pipeline = facts.get("pipeline")
    if pipeline:
        return _check_pipeline(str(pipeline), runtime)
    base = {
        "model_type": model_type,
        "architectures": architectures,
        "model_transformers_version": saved_with,
        "vllm_version": runtime.vllm_version,
        "transformers_version": runtime.transformers_version,
    }
    if not model_type and not architectures:
        return Compat(status=UNKNOWN, code="no_config", **base)
    if facts.get("needs_remote_code"):
        # The repository registers its own model type and architecture.
        return Compat(status=UNKNOWN, code="remote_code", **base)

    engine = f"vLLM {runtime.vllm_version}" if runtime.vllm_version else "vLLM"
    library = f"Transformers {runtime.transformers_version}" if runtime.transformers_version else "Transformers"
    saved = f" (it was saved with Transformers {saved_with})" if saved_with else ""

    if runtime.model_types and model_type and model_type not in runtime.model_types:
        return Compat(
            status=CANNOT_RUN,
            code="model_type_unknown",
            message=(
                f"This cluster's runtime cannot load this model: neither {library} nor {engine} in it "
                f"knows its model type '{model_type}'{saved}. It needs a newer runtime image."
            ),
            **base,
        )
    if runtime.architectures and architectures and not any(a in runtime.architectures for a in architectures):
        return Compat(
            status=MAY_NOT_RUN,
            code="architecture_not_native",
            message=(
                f"{engine} in this cluster's runtime has no implementation of {', '.join(architectures)}. "
                f"It can only try its generic Transformers backend, which not every model supports."
            ),
            **base,
        )
    if not runtime.model_types and _newer(saved_with, runtime.transformers_version):
        return Compat(
            status=MAY_NOT_RUN,
            code="newer_transformers",
            message=(
                f"This model was saved with Transformers {saved_with}, newer than the {library} in this "
                f"cluster's runtime; it may not know the model type '{model_type}'."
            ),
            **base,
        )
    if not runtime.model_types and not runtime.architectures:
        return Compat(status=UNKNOWN, **base)
    return Compat(status=RUNS, **base)


def _check_pipeline(pipeline: str, runtime: RuntimeSupport) -> Compat:
    """A picture model on a runtime: its image app has the pipeline, or not."""
    base = {"architectures": [pipeline], "vllm_version": runtime.vllm_version,
            "transformers_version": runtime.transformers_version}
    if runtime.pipelines:
        if pipeline in runtime.pipelines:
            return Compat(status=RUNS, **base)
        diffusers = f"diffusers {runtime.diffusers_version}" if runtime.diffusers_version else "diffusers"
        return Compat(
            status=CANNOT_RUN,
            code="pipeline_unknown",
            message=(
                f"This cluster's runtime cannot load this model: {diffusers} in it has no {pipeline}. "
                "It needs a newer runtime image."
            ),
            **base,
        )
    if runtime.model_types or runtime.architectures:
        # A runtime whose lists are known, and none of them pictures: it has no image app.
        return Compat(
            status=CANNOT_RUN,
            code="no_image_app",
            message=(
                "This cluster's runtime serves no picture models. Switch the cluster to a runtime "
                "that has them (its Runtime card), then host it."
            ),
            **base,
        )
    return Compat(status=UNKNOWN, **base)
