"""The text watermark the runtimes put into answers (P6): which deployments start with it, how, and what is published.

The backend gives the installation's current watermark key to the chat deployments it starts on a runtime with the
watermark (vLLM 0.30 and later), publishes how they watermark -- never the key -- for the gateway's receipts, and
keeps a deployment's key version until it is applied again for another reason.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from faden_backend.services.inference import publication
from faden_backend.services.inference.drivers.ray.compiler import compile_deployment
from faden_backend.services.inference.engine_guard import CODE_SETTINGS
from faden_backend.services.llm.gateway_sync import GatewaySyncService
from faden_backend.services.provenance import watermark
from faden_backend.settings import settings
from tests.test_gateway_sync_phase5 import FakeAsyncSession

KEY = (2, 8_314_559_120_734_552_031)


def _spec(**config: Any) -> dict[str, Any]:
    return {
        "api_version": "inference.fadenstack.com/v1alpha1",
        "engine": {"name": "vllm", "config": dict(config)},
        "scale": {"replicas": 1},
        "resources": {"replica": {"gpus": 1.0}},
        "service": {"path": "/v1"},
    }


def _engine(spec: dict[str, Any], key: tuple[int, int] | None = KEY) -> dict[str, Any]:
    doc = compile_deployment(
        spec_data=spec, model_display_name="Qwen/Qwen3.8-27B-FP8", model_source="remote", watermark_key=key,
    )
    return doc["llm_configs"][0]["engine_kwargs"]


# ── The keys ──────────────────────────────────────────────────────


def test_the_keys_are_read_with_their_versions_and_the_highest_is_current() -> None:
    keys = watermark.parse_keys("1:42, 3:8314559120734552031")
    assert keys.versions == {1: 42, 3: 8_314_559_120_734_552_031}
    assert keys.current == 3
    assert keys.pick(1) == (1, 42), "a deployment keeps its version"
    assert keys.pick(2) == (3, 8_314_559_120_734_552_031), "a version no longer kept: the current one"
    assert keys.pick(None) == (3, 8_314_559_120_734_552_031)
    assert watermark.parse_keys("").pick(None) is None


@pytest.mark.parametrize("value", ["1:abc", "x:1", "1:0", f"1:{2**64}", "1"])
def test_keys_that_do_not_read_are_refused_without_their_value(value: str) -> None:
    with pytest.raises(watermark.WatermarkKeyError) as refused:
        watermark.parse_keys(value)
    assert "abc" not in str(refused.value) and str(2**64) not in str(refused.value)


def test_the_keys_count_only_with_the_module_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provenance_watermark_keys", "1:42")
    monkeypatch.setattr(settings, "provenance_enabled", True)
    assert watermark.installed_keys() == watermark.WatermarkKeys({1: 42})
    monkeypatch.setattr(settings, "provenance_enabled", False)
    assert watermark.installed_keys() is None
    monkeypatch.setattr(settings, "provenance_enabled", True)
    monkeypatch.setattr(settings, "provenance_watermark_keys", "1:@@@")
    assert watermark.installed_keys() is None, "unreadable: models start without it, nothing fails"


@pytest.mark.parametrize(("version", "has_it"), [
    ("0.31.0", True), ("0.30.0", True), ("v0.30.1rc1", True), ("1.0.0", True),
    ("0.29.0", False), ("0.10.2", False), ("", False), (None, False),
])
def test_only_a_runtime_from_vllm_0_30_has_the_watermark(version: str | None, has_it: bool) -> None:
    assert watermark.runtime_has_it(version) is has_it


# ── The compiled deployment ───────────────────────────────────────


def test_a_chat_model_starts_with_the_current_key() -> None:
    engine = _engine(_spec())
    assert engine["watermark_config"] == {"algorithm": "gumbel", "context_width": 4, "key": KEY[1]}


def test_with_speculative_decoding_both_keys_and_probabilistic_drafts() -> None:
    engine = _engine(_spec(speculative_config='{"method": "mtp", "num_speculative_tokens": 3}'))
    assert engine["watermark_config"]["algorithm"] == "dual_key_gumbel"
    assert engine["speculative_config"] == {
        "method": "mtp", "num_speculative_tokens": 3,
        "draft_sample_method": "probabilistic", "rejection_sample_method": "standard",
    }


@pytest.mark.parametrize("speculative", [
    {"method": "ngram", "num_speculative_tokens": 3},
    {"method": "dspark", "parallel_drafting": True},
], ids=["ngram", "parallel-drafting"])
def test_a_speculative_decoding_vllm_does_not_watermark_starts_without_it(speculative: dict[str, Any]) -> None:
    engine = _engine(_spec(speculative_config=speculative))
    assert "watermark_config" not in engine
    assert "draft_sample_method" not in engine["speculative_config"], "left as the operator set it"


@pytest.mark.parametrize("config", [
    {"task": "embed"}, {"runner": "pooling", "convert": "classify"},
], ids=["embeddings", "scoring"])
def test_a_model_that_generates_no_text_gets_no_key(config: dict[str, Any]) -> None:
    assert "watermark_config" not in _engine(_spec(**config))


def test_a_spec_never_brings_its_own_key() -> None:
    own = _engine(_spec(watermark_config={"key": 7, "context_width": 6, "algorithm": "dual_key_gumbel"}))
    assert own["watermark_config"] == {"algorithm": "gumbel", "context_width": 6, "key": KEY[1]}
    off_here = _engine(_spec(watermark_config={"key": 7}), key=None)
    assert "watermark_config" not in off_here, "with no key given, a spec's own never reaches vLLM"


@pytest.mark.parametrize("off", [False, "false", "off"])
def test_a_deployment_switched_off_starts_without_it(off: Any) -> None:
    assert "watermark_config" not in _engine(_spec(watermark_config=off))


def test_switching_it_off_is_a_superuser_setting() -> None:
    assert "watermark_config" in CODE_SETTINGS


def test_what_is_published_names_the_version_never_the_key() -> None:
    doc = compile_deployment(
        spec_data=_spec(), model_display_name="Qwen/Qwen3.8-27B-FP8", model_source="remote", watermark_key=KEY,
    )
    published = watermark.published(doc, watermark.WatermarkKeys({1: 42, KEY[0]: KEY[1]}))
    assert published == {"algorithm": "gumbel", "key_version": 2, "context_width": 4}
    assert str(KEY[1]) not in str(published)
    plain = compile_deployment(spec_data=_spec(), model_display_name="m", model_source="remote")
    assert watermark.published(plain, watermark.WatermarkKeys({1: 42})) is None


# ── The driver's choice and the gateway's routing row ─────────────


def _facts(vllm: str | None) -> Any:
    matrix = SimpleNamespace(vllm_version=vllm) if vllm is not None else None
    return SimpleNamespace(runtime_bundle=SimpleNamespace(compatibility_matrix=matrix) if matrix else None)


def test_the_driver_gives_a_key_only_on_a_runtime_with_the_watermark(monkeypatch: pytest.MonkeyPatch) -> None:
    from faden_backend.services.inference.drivers.ray.deployment import RayDeploymentManager

    monkeypatch.setattr(settings, "provenance_enabled", True)
    monkeypatch.setattr(settings, "provenance_watermark_keys", "1:42,2:43")
    choose = RayDeploymentManager._watermark_key  # noqa: SLF001
    assert choose(None, _facts("0.31.0"), None) == (2, 43)  # type: ignore[arg-type]
    assert choose(None, _facts("0.31.0"), 1) == (1, 42), "kept until applied again"  # type: ignore[arg-type]
    assert choose(None, _facts("0.29.0"), None) is None  # type: ignore[arg-type]
    assert choose(None, _facts(None), None) is None, "Ray on the host: not known"  # type: ignore[arg-type]


def test_publication_passes_only_how_it_watermarks() -> None:
    published = {"algorithm": "dual_key_gumbel", "key_version": 2, "context_width": 4, "key": 43}
    assert publication._watermark(published) == {  # noqa: SLF001
        "algorithm": "dual_key_gumbel", "key_version": 2, "context_width": 4,
    }
    assert publication._watermark(None) is None  # noqa: SLF001
    assert publication._watermark({"key_version": 2}) is None  # noqa: SLF001


@pytest.mark.anyio
async def test_the_routing_row_tells_the_gateway_how_the_answers_are_watermarked() -> None:
    session = FakeAsyncSession(select_responses=[None])
    service = GatewaySyncService(lambda: session)
    mark = {"algorithm": "gumbel", "key_version": 2, "context_width": 4}
    await service.publish_inference_endpoint(
        deployment_id=uuid.uuid4(), base_url="http://192.0.2.71:8000/faden-x/v1", alias="qwen",
        served_model_name="Qwen/Qwen3.8-27B-FP8", watermark=mark,
    )
    insert = next(params for sql, params in session.executed if "INSERT INTO llm_provider_instance" in sql)
    assert json.loads(insert["node_metadata"]) == {"task": "chat", "watermark": mark}
