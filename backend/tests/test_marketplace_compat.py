"""The verdicts, one rule each, and against what the runtime images really list."""

from __future__ import annotations

import pytest

from faden_backend.services.inference.bundles import default_bundle_registry
from faden_backend.services.marketplace import compat
from faden_backend.services.marketplace.compat import RuntimeSupport

QWEN4 = {"model_type": "qwen4_exp", "architectures": ["Qwen4ExpForConditionalGeneration"],
         "transformers_version": "5.16.0.dev0", "needs_remote_code": False}
QWEN3 = {"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"], "transformers_version": "4.51.0",
         "needs_remote_code": False}

GB10 = "faden-ray-vllm-gb10-ray2.58-nv26.08"
GB10_PREVIEW = "faden-ray-vllm-gb10-ray2.58-nv26.09"
X86 = "faden-ray-vllm-x86_64-ray2.58"


def _runtime(runtime_id: str) -> RuntimeSupport:
    matrix = default_bundle_registry.get_bundle(runtime_id).compatibility_matrix
    return RuntimeSupport(vllm_version=matrix.vllm_version, transformers_version=matrix.transformers_version,
                          model_types=frozenset(matrix.supported_model_types),
                          architectures=frozenset(matrix.supported_architectures))


def test_the_dgx_runtime_cannot_load_qwen4_exp() -> None:
    verdict = compat.check(QWEN4, _runtime(GB10))
    assert verdict.status == compat.CANNOT_RUN and verdict.code == "model_type_unknown"


def test_the_dgx_preview_runtime_runs_qwen4_exp() -> None:
    """Why it ships: NVIDIA 26.09's vLLM 0.29 and Transformers 5.16 know Qwen3.8."""
    assert compat.check(QWEN4, _runtime(GB10_PREVIEW)).status == compat.RUNS


def test_the_x86_runtime_reads_qwen4_exp_but_has_no_implementation_of_it() -> None:
    """Its Transformers 5.17 knows the model type; its vLLM 0.26 does not know the architecture."""
    verdict = compat.check(QWEN4, _runtime(X86))
    assert verdict.status == compat.MAY_NOT_RUN and verdict.code == "architecture_not_native"


@pytest.mark.parametrize("runtime_id", [GB10, GB10_PREVIEW, X86])
def test_every_runtime_runs_qwen3(runtime_id: str) -> None:
    assert compat.check(QWEN3, _runtime(runtime_id)).status == compat.RUNS


def test_a_repository_with_its_own_code_is_not_judged_by_the_lists() -> None:
    assert compat.check(QWEN4 | {"needs_remote_code": True}, _runtime(GB10)).status == compat.UNKNOWN


def test_without_lists_a_newer_transformers_is_a_warning() -> None:
    runtime = RuntimeSupport(vllm_version="0.27.1", transformers_version="5.14.1")
    assert compat.check(QWEN4, runtime).code == "newer_transformers"
    assert compat.check(QWEN3, runtime).status == compat.UNKNOWN


def test_no_config_says_nothing() -> None:
    assert compat.check({}, _runtime(GB10)).status == compat.UNKNOWN
    assert compat.check(QWEN4, None).status == compat.UNKNOWN, "a cluster whose runtime is not known"
