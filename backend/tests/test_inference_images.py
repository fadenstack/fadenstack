"""Picture models on a cluster (sdk/11): compiled for the runtime's image app, published as images.

A deployment with ``engine.name: diffusers`` compiles to the runtime image's own document
(``image_configs``), not Serve LLM's; its GPU share goes on the copy's actor. ``resources.replica.machine``
pins every copy, of either kind, to one machine through Ray's per-node resource.
"""

from __future__ import annotations

import pytest

from faden_backend.services.inference.drivers.ray.compiler import (
    DeploymentValidationError,
    compile_deployment,
)
from faden_backend.services.inference.drivers.ray.deployment import (
    _fixed_count,
    _model_server_deployments,
    shape_hash,
)
from faden_backend.services.inference.publication import IMAGE_REQUESTS_PER_COPY, gateway_concurrency
from faden_backend.services.llm.kinds import deployment_kind


def _spec(**overrides: object) -> dict:
    base = {
        "api_version": "inference.fadenstack.com/v1alpha1",
        "engine": {"name": "diffusers", "config": {"num_inference_steps": 9, "guidance_scale": 0.0}},
        "scale": {"replicas": 1},
        "resources": {"replica": {"gpus": 0.2}},
        "service": {"path": "/v1", "alias": "pictures"},
    }
    base.update(overrides)
    return base


def _compile(spec: dict, **kwargs: object) -> dict:
    return compile_deployment(
        spec_data=spec,
        model_display_name="Z-Image-Turbo",
        model_source="remote",
        hf_repo_id="Tongyi-MAI/Z-Image-Turbo",
        **kwargs,
    )


def test_a_picture_model_compiles_to_the_image_app() -> None:
    doc = _compile(_spec(), node_resource="node:10.100.0.1")
    assert "llm_configs" not in doc
    (config,) = doc["image_configs"]
    assert config["model_loading_config"] == {"model_id": "Z-Image-Turbo", "model_source": "Tongyi-MAI/Z-Image-Turbo"}
    # The picture settings as given, bfloat16 unless said otherwise.
    assert config["pipeline_config"] == {"num_inference_steps": 9, "guidance_scale": 0.0, "dtype": "bfloat16"}
    deployment = config["deployment_config"]
    assert deployment["num_replicas"] == 1
    # Its share of the device on the copy's actor, and the pinned machine.
    assert deployment["ray_actor_options"] == {"num_gpus": 0.2, "resources": {"node:10.100.0.1": 0.001}}


def test_a_stopped_picture_model_runs_no_copies() -> None:
    doc = _compile(_spec(), desired_state="stopped")
    assert doc["image_configs"][0]["deployment_config"]["num_replicas"] == 0


def test_a_picture_model_takes_one_whole_device_unless_told() -> None:
    spec = _spec(resources={"replica": {}})
    actor = _compile(spec)["image_configs"][0]["deployment_config"]["ray_actor_options"]
    assert actor == {"num_gpus": 1.0}


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"engine": {"name": "diffusers", "config": {"max_model_len": 4096}}}, "not a picture setting"),
        ({"engine": {"name": "diffusers", "config": {"dtype": "int8"}}}, "dtype"),
        ({"engine": {"name": "diffusers", "config": {"size": "big"}}}, "WIDTHxHEIGHT"),
        ({"engine": {"name": "diffusers", "config": {"num_inference_steps": 0}}}, "positive whole number"),
        ({"topology": {"tensor_parallel_size": 2}, "resources": {"replica": {"gpus": 2}}}, "one device"),
    ],
)
def test_what_a_picture_model_refuses(change: dict, says: str) -> None:
    with pytest.raises(DeploymentValidationError, match=says):
        _compile(_spec(**change))


def test_a_pinned_chat_model_asks_for_the_machine_in_every_bundle() -> None:
    spec = _spec(engine={"name": "vllm", "config": {}}, resources={"replica": {"gpus": 0.7}})
    doc = _compile(spec, node_resource="node:10.100.0.2")
    bundle = doc["llm_configs"][0]["placement_group_config"]["bundle_per_worker"]
    assert bundle == {"GPU": 0.7, "node:10.100.0.2": 0.001}


def test_an_unpinned_whole_gpu_chat_model_keeps_rays_default_placement() -> None:
    spec = _spec(engine={"name": "vllm", "config": {}}, resources={"replica": {"gpus": 1}})
    assert "placement_group_config" not in _compile(spec)["llm_configs"][0]


def test_the_orchestrator_reads_a_picture_app_like_an_llm_one() -> None:
    doc = _compile(_spec(scale={"replicas": 2}))
    assert _fixed_count(doc) == 2
    # The copy count is not part of the shape: changing it scales in place.
    assert shape_hash(doc) == shape_hash(_compile(_spec(scale={"replicas": 1})))
    app = {"deployments": {
        "ImageServer:Z-Image-Turbo": {"num_replicas_ready": 1},
        "Something:else": {"num_replicas_ready": 0},
    }}
    assert list(_model_server_deployments(app)) == ["ImageServer:Z-Image-Turbo"]


@pytest.mark.parametrize(
    ("engine", "kind"),
    [
        ({"name": "diffusers", "config": {}}, "images"),
        ({"name": "vllm", "config": {}}, "chat"),
        ({"name": "vllm", "config": {"runner": "pooling"}}, "embeddings"),
        ({"name": "vllm", "config": {"runner": "pooling", "convert": "classify"}}, "scoring"),
        ({"name": "vllm", "config": {"convert": "embed"}}, "embeddings"),
    ],
)
def test_what_a_deployment_is_published_as(engine: dict, kind: str) -> None:
    assert deployment_kind({"engine": engine}) == kind


class _Deployment:
    def __init__(self, spec: dict, ready: int) -> None:
        self.spec_json = spec
        self.ready_replicas = ready


def test_the_gateway_lets_a_few_picture_requests_through_per_copy() -> None:
    assert gateway_concurrency(_Deployment(_spec(), 2)) == 2 * IMAGE_REQUESTS_PER_COPY
    assert gateway_concurrency(_Deployment({"engine": {"name": "vllm", "config": {"max_num_seqs": 8}}}, 2)) == 16
