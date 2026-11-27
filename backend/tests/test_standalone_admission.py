"""What a Mac must be able to do before a standalone environment uses it, and its memory budget."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from faden_backend.services.inference.drivers.standalone import admission

GiB = 2**30


def mac(**caps: Any) -> SimpleNamespace:
    base = {"native_runtime": 1, "model_tls_native": 1}
    base.update(caps)
    return SimpleNamespace(id="n", agent_id="studio", host="10.0.0.9", capabilities_json=base)


def test_a_capable_mac_is_admitted() -> None:
    assert admission.node_refusal(mac()) is None


@pytest.mark.parametrize(
    ("caps", "words"),
    [
        ({"native_runtime": 0}, "run a native model runtime"),
        ({"model_tls_native": None}, "model-TLS proxy"),
        ({"native_runtime": 0, "model_tls_native": 0}, " or "),
    ],
)
def test_a_mac_whose_agent_cannot_serve_is_refused_with_a_sentence(caps: dict[str, Any], words: str) -> None:
    problem = admission.node_refusal(mac(**caps))
    assert problem is not None
    assert "studio" in problem and words in problem and "Update the agent" in problem


def test_the_working_set_is_the_reported_one_when_there_is_one() -> None:
    node = mac(metal={"recommended_max_working_set_bytes": 55_662_788_608, "unified_memory_bytes": 68_719_476_736})
    assert admission.working_set_bytes(node) == (55_662_788_608, False)


def test_before_staging_the_working_set_is_estimated_from_unified_memory() -> None:
    node = mac(metal={"unified_memory_bytes": 64 * GiB})
    value, estimated = admission.working_set_bytes(node)
    assert estimated
    assert value == int(64 * GiB * 0.81)


def test_a_budget_is_a_fraction_of_the_working_set() -> None:
    node = mac(metal={"recommended_max_working_set_bytes": 40 * GiB})
    budget = admission.memory_budget(node, "20Gi")
    assert budget["gpu_memory_utilization"] == 0.5
    assert budget["estimated"] is False


def test_an_estimated_budget_says_so() -> None:
    budget = admission.memory_budget(mac(metal={"unified_memory_bytes": 64 * GiB}), "16Gi")
    assert budget["estimated"] is True
    assert 0 < budget["gpu_memory_utilization"] < 1


def test_no_budget_runs_with_the_gate0_default() -> None:
    budget = admission.memory_budget(mac(), None)
    assert budget["gpu_memory_utilization"] == admission.DEFAULT_GPU_MEMORY_UTILIZATION


def test_a_budget_that_does_not_fit_is_refused() -> None:
    with pytest.raises(ValueError, match="asks for 60.0 GiB"):
        admission.memory_budget(mac(metal={"recommended_max_working_set_bytes": 40 * GiB}), "60Gi")


def test_a_budget_on_a_mac_that_reported_no_memory_is_refused() -> None:
    with pytest.raises(ValueError, match="has not reported its memory"):
        admission.memory_budget(mac(), "8Gi")


@pytest.mark.parametrize(
    ("text", "value"),
    [("32Gi", 32 * GiB), ("512Mi", 512 * 2**20), ("1G", 10**9), ("2048", 2048), ("8GiB", 8 * GiB), (None, None)],
)
def test_parse_bytes(text: str | None, value: int | None) -> None:
    assert admission.parse_bytes(text) == value
