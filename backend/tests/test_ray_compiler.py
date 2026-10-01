"""Unit tests for the pure Ray Serve deployment compiler."""

import pytest
from faden_backend.services.inference.drivers.ray.compiler import (
    DeploymentValidationError,
    compile_deployment,
    validate_spec,
)
from faden_backend.services.inference.schemas import parse_inference_deployment_spec


def _base_spec(**overrides) -> dict:
    base = {
        "api_version": "inference.fadenstack.com/v1alpha1",
        "engine": {"name": "vllm", "config": {}},
        "scale": {"replicas": 1},
        "resources": {"replica": {"gpus": 1.0}},
        "service": {"path": "/v1"},
    }
    base.update(overrides)
    return base


def test_compile_fixed_replicas() -> None:
    doc = compile_deployment(
        spec_data=_base_spec(scale={"replicas": 2}),
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
    )
    llm_config = doc["llm_configs"][0]
    dep_cfg = llm_config["deployment_config"]
    assert dep_cfg["num_replicas"] == 2
    assert "autoscaling_config" not in dep_cfg


def test_compile_autoscale_does_not_emit_num_replicas() -> None:
    spec = _base_spec(
        scale={"autoscale": {"min_replicas": 1, "max_replicas": 5, "scale_up_timeout": 10}},
        extensions={"ray": {"target_ongoing_requests": 4}},
    )
    doc = compile_deployment(
        spec_data=spec,
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
    )
    llm_config = doc["llm_configs"][0]
    dep_cfg = llm_config["deployment_config"]
    assert "autoscaling_config" in dep_cfg
    assert dep_cfg["autoscaling_config"]["min_replicas"] == 1
    assert dep_cfg["autoscaling_config"]["max_replicas"] == 5
    assert dep_cfg["autoscaling_config"]["target_ongoing_requests"] == 4.0
    assert dep_cfg["autoscaling_config"]["upscale_delay_s"] == 10.0
    # Crucial: num_replicas must NOT be present when autoscaling_config is set
    assert "num_replicas" not in dep_cfg


def test_compile_autoscale_min_replicas_zero() -> None:
    spec = _base_spec(
        scale={"autoscale": {"min_replicas": 0, "max_replicas": 3}}
    )
    doc = compile_deployment(
        spec_data=spec,
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
    )
    llm_config = doc["llm_configs"][0]
    dep_cfg = llm_config["deployment_config"]
    assert dep_cfg["autoscaling_config"]["min_replicas"] == 0
    assert "num_replicas" not in dep_cfg


def test_compile_stopped_desired_state() -> None:
    spec = _base_spec(
        scale={"autoscale": {"min_replicas": 1, "max_replicas": 5}}
    )
    doc = compile_deployment(
        spec_data=spec,
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
        desired_state="stopped",
    )
    llm_config = doc["llm_configs"][0]
    dep_cfg = llm_config["deployment_config"]
    assert dep_cfg["num_replicas"] == 0
    assert "autoscaling_config" not in dep_cfg


def test_compile_threads_revision() -> None:
    spec = _base_spec(
        artifacts={"source": "remote", "revision": "v1.2.3"}
    )
    doc = compile_deployment(
        spec_data=spec,
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
        hf_revision="commit-abc",
    )
    llm_config = doc["llm_configs"][0]
    # Artifact revision takes precedence
    assert llm_config["engine_kwargs"]["revision"] == "v1.2.3"

    # Fallback to hf_revision if spec does not specify one
    spec2 = _base_spec(artifacts={"source": "remote"})
    doc2 = compile_deployment(
        spec_data=spec2,
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
        hf_revision="commit-abc",
    )
    assert doc2["llm_configs"][0]["engine_kwargs"]["revision"] == "commit-abc"


def test_compile_threads_runtime_env() -> None:
    spec = _base_spec(
        extensions={"env_vars": {"VLLM_ATTENTION_BACKEND": "FLASH_ATTN"}}
    )
    doc = compile_deployment(
        spec_data=spec,
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
        runtime_env={"pip": ["transformers"]},
    )
    llm_config = doc["llm_configs"][0]
    assert "runtime_env" in llm_config
    assert llm_config["runtime_env"]["env_vars"] == {"VLLM_ATTENTION_BACKEND": "FLASH_ATTN"}
    assert llm_config["runtime_env"]["pip"] == ["transformers"]


def test_validation_rejects_unsupported_engine() -> None:
    spec = _base_spec(engine={"name": "tgi"})
    with pytest.raises(DeploymentValidationError, match="unsupported engine"):
        compile_deployment(
            spec_data=spec,
            model_display_name="m",
            model_source="remote",
        )


def test_validation_rejects_kv_aware_routing() -> None:
    spec = _base_spec(replica_routing={"kv_aware": True})
    with pytest.raises(DeploymentValidationError, match="KV-aware"):
        compile_deployment(
            spec_data=spec,
            model_display_name="m",
            model_source="remote",
        )


def test_validation_rejects_min_greater_than_max_replicas() -> None:
    spec = _base_spec(scale={"autoscale": {"min_replicas": 5, "max_replicas": 2}})
    with pytest.raises(DeploymentValidationError, match="min_replicas .* cannot be greater than max_replicas"):
        compile_deployment(
            spec_data=spec,
            model_display_name="m",
            model_source="remote",
        )


def _llm_config(**overrides) -> dict:
    return compile_deployment(
        spec_data=_base_spec(**overrides), model_display_name="m", model_source="remote"
    )["llm_configs"][0]


def test_target_utilization_is_rejected_not_mis_mapped() -> None:
    """A 0..1 fraction is not a request count; mapping it would scale to max."""
    spec = _base_spec(scale={"autoscale": {"min_replicas": 1, "max_replicas": 3, "target_utilization": 0.8}})
    with pytest.raises(DeploymentValidationError, match="target_ongoing_requests"):
        compile_deployment(spec_data=spec, model_display_name="m", model_source="remote")


def test_multi_gpu_replica_keeps_ray_default_placement() -> None:
    """gpus == TP x PP -> Ray's per-device bundles with PACK (cross-node).
    A single {"GPU": n} STRICT_PACK bundle cannot be placed on 1-GPU nodes."""
    cfg = _llm_config(resources={"replica": {"gpus": 2}}, topology={"tensor_parallel_size": 2})
    assert "placement_group_config" not in cfg
    assert cfg["engine_kwargs"]["tensor_parallel_size"] == 2


def test_gpus_inconsistent_with_topology_is_rejected() -> None:
    with pytest.raises(DeploymentValidationError, match="must equal tensor_parallel_size"):
        _llm_config(resources={"replica": {"gpus": 2}})


def test_fractional_gpu_uses_bundle_per_worker_without_accelerator_key() -> None:
    cfg = _llm_config(resources={"replica": {"gpus": 0.5, "accelerator": "A100"}})
    assert cfg["placement_group_config"] == {"bundle_per_worker": {"GPU": 0.5}}
    assert cfg["accelerator_type"] == "A100"  # Ray adds its own fractional hint


def test_topology_nodes_maps_to_placement_strategy() -> None:
    """Rule lock (Phase3_upgrade.md section 15), proven on the DGX Spark pair.

    nodes == 1 -> STRICT_PACK, nodes > 1 -> SPREAD, unset -> Ray's default
    PACK.  Without the mapping ``topology.nodes`` is a no-op: under soft PACK
    a ``nodes: 1`` TP group can still spill across two hosts (silent tensor
    parallelism over the network) and ``nodes: 2`` can pack onto one host.
    The certified TP=2 run used an explicit SPREAD.
    """
    cfg_spread = _llm_config(
        topology={"tensor_parallel_size": 2, "nodes": 2}, resources={"replica": {}}
    )
    assert cfg_spread["placement_group_config"] == {
        "bundle_per_worker": {"GPU": 1.0},
        "strategy": "SPREAD",
    }

    cfg_strict = _llm_config(
        topology={"tensor_parallel_size": 1, "nodes": 1}, resources={"replica": {}}
    )
    assert cfg_strict["placement_group_config"] == {
        "bundle_per_worker": {"GPU": 1.0},
        "strategy": "STRICT_PACK",
    }

    # nodes unset: no config emitted, Ray's default soft PACK applies.
    cfg_default = _llm_config(
        topology={"tensor_parallel_size": 2}, resources={"replica": {}}
    )
    assert "placement_group_config" not in cfg_default

    # An explicit operator strategy still wins over the derived one.
    cfg_ext = _llm_config(
        topology={"tensor_parallel_size": 2, "nodes": 2},
        resources={"replica": {}},
        extensions={"ray": {"placementStrategy": "PACK"}},
    )
    assert cfg_ext["placement_group_config"] == {
        "bundle_per_worker": {"GPU": 1.0},
        "strategy": "PACK",
    }

    # Explicit placement strategy via resources.placement
    cfg_pack = _llm_config(
        topology={"tensor_parallel_size": 2, "nodes": 1},
        resources={"placement": "PACK", "replica": {"gpus": 2}},
    )
    assert cfg_pack["placement_group_config"] == {"bundle_per_worker": {"GPU": 1.0}, "strategy": "PACK"}


def test_strict_pack_is_still_refused_for_multi_node_topologies() -> None:
    """The deadlock guard survives the restored mapping."""
    with pytest.raises(DeploymentValidationError):
        _llm_config(
            topology={"tensor_parallel_size": 2, "nodes": 2},
            resources={"placement": "STRICT_PACK", "replica": {}},
        )


@pytest.mark.parametrize(
    "extensions",
    [
        {"runtime_env": {"pip": ["anything"]}},
        {"ray": {"runtime_env": {"working_dir": "https://example.com/code.zip"}}},
        {"env_vars": {"CUDA_VISIBLE_DEVICES": "0"}},
        {"env_vars": {"PATH": "/tmp"}},
        # Every stack has its own way to say "show me other devices", and each
        # one is the same escape from the scheduler's allocation.
        {"env_vars": {"HIP_VISIBLE_DEVICES": "0"}},
        {"env_vars": {"ROCR_VISIBLE_DEVICES": "0"}},
        {"env_vars": {"ZE_AFFINITY_MASK": "0"}},
        {"env_vars": {"ONEAPI_DEVICE_SELECTOR": "level_zero:0"}},
    ],
)
def test_spec_cannot_inject_code_or_override_gpu_assignment(extensions) -> None:
    with pytest.raises(DeploymentValidationError):
        _llm_config(extensions=extensions)


def test_allowlisted_env_vars_reach_runtime_env() -> None:
    cfg = _llm_config(
        extensions={"ray": {"runtime_env": {"env_vars": {"VLLM_WSL2_ENABLE_PIN_MEMORY": "1"}}}}
    )
    assert cfg["runtime_env"] == {"env_vars": {"VLLM_WSL2_ENABLE_PIN_MEMORY": "1"}}


@pytest.mark.parametrize(
    "name",
    [
        "NCCL_IB_DISABLE",       # NVIDIA collectives
        "RCCL_MSCCL_ENABLE",     # AMD collectives
        "HSA_OVERRIDE_GFX_VERSION",
        "ZE_ENABLE_TRACING_LAYER",
        "SYCL_CACHE_PERSISTENT",
    ],
)
def test_each_stack_can_be_tuned_through_its_own_names(name: str) -> None:
    """A non-NVIDIA node has no CUDA_/NCCL_ knobs, only its own."""
    cfg = _llm_config(extensions={"env_vars": {name: "1"}})
    assert cfg["runtime_env"]["env_vars"] == {name: "1"}


def test_strict_pack_with_multi_node_topology_is_rejected() -> None:
    """STRICT_PACK requires all bundles on 1 host; specifying nodes > 1 is a fatal deadlock risk."""
    with pytest.raises(DeploymentValidationError, match="STRICT_PACK.*deadlock"):
        _llm_config(
            resources={"placement": "STRICT_PACK", "replica": {"gpus": 2}},
            topology={"tensor_parallel_size": 2, "nodes": 2},
        )




# ── Prefix-aware routing between copies ──────────────────────────────────────

PREFIX_ROUTER = "ray.serve.llm.request_router.PrefixCacheAffinityRouter"


@pytest.mark.parametrize(
    ("scale", "desired_state", "strategy", "routed"),
    [
        ({"replicas": 2}, "active", "prefix_affinity", True),
        ({"autoscale": {"min_replicas": 1, "max_replicas": 3}}, "active", "prefix_affinity", True),
        ({"replicas": 1}, "active", "prefix_affinity", False),
        ({"replicas": 2}, "stopped", "prefix_affinity", False),
        # Not asked for: Serve's default router. The prefix router's ingress
        # stopped answering health checks under 16 concurrent chats.
        ({"replicas": 2}, "active", "default", False),
        ({"replicas": 2}, "active", None, False),
    ],
    ids=["two-copies", "may-scale-to-three", "one-copy", "stopped", "default-strategy", "not-asked"],
)
def test_a_deployment_that_asks_for_it_routes_a_chat_to_the_copy_holding_its_prefix(
    scale: dict, desired_state: str, strategy: str | None, routed: bool,
) -> None:
    """Serve's default router sent every other turn to a copy without its KV cache."""
    routing = {"replica_routing": {"strategy": strategy}} if strategy else {}
    doc = compile_deployment(
        spec_data=_base_spec(scale=scale, **routing),
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
        desired_state=desired_state,
    )
    router = doc["llm_configs"][0]["deployment_config"].get("request_router_config")
    if not routed:
        assert router is None
        return
    assert router["request_router_class"] == PREFIX_ROUTER
    # Balances again past this queue difference; the tree is kept bounded.
    assert router["request_router_kwargs"] == {"imbalanced_threshold": 10, "do_eviction": True}


def test_json_settings_reach_vllm_as_objects() -> None:
    """The editor keeps them as JSON text, and Ray Serve hands that to vLLM's Python side as it is.

    vLLM then failed every copy with "'str' object has no attribute 'items'"
    for an MTP ``speculative_config`` (.61, 2026-10-01).
    """
    doc = compile_deployment(
        spec_data=_base_spec(engine={"name": "vllm", "config": {
            "speculative_config": '{"method": "mtp", "num_speculative_tokens": 3}',
            "compilation_config": " ",
            "chat_template": "{% for m in messages %}{{ m.content }}{% endfor %}",
            "max_model_len": 4096,
        }}),
        model_display_name="meta-llama/Llama-3-8B",
        model_source="remote",
    )
    kwargs = doc["llm_configs"][0]["engine_kwargs"]
    assert kwargs["speculative_config"] == {"method": "mtp", "num_speculative_tokens": 3}
    assert "compilation_config" not in kwargs, "empty text is the setting left unset"
    assert kwargs["chat_template"].startswith("{% for"), "a template is text, not JSON"
    assert kwargs["max_model_len"] == 4096


def test_a_json_setting_that_does_not_parse_is_refused_before_serve_tries_it() -> None:
    with pytest.raises(ValueError, match="speculative_config is not valid JSON"):
        compile_deployment(
            spec_data=_base_spec(engine={"name": "vllm", "config": {"speculative_config": "{method: mtp}"}}),
            model_display_name="meta-llama/Llama-3-8B",
            model_source="remote",
        )
