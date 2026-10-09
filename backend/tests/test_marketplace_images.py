"""Picture models in the marketplace (sdk/11): found, sized, checked against the runtime, hosted.

The model is Tongyi-MAI/Z-Image-Turbo as the Hub describes it (2026-10-09): a diffusers pipeline whose
transformer is stored in float32 (24.6 GB) beside a 8.0 GB text encoder; the image app loads it in
bfloat16, so it takes about 20.5 GB.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from faden_backend.services.marketplace import compat, facts, fit, hub, settings
from faden_backend.services.marketplace.hardware import gpus_from_utilization
from faden_backend.services.marketplace.host import HostError, HostRequest, build_spec

GB10_MIB = 124_610

Z_IMAGE: dict[str, Any] = {
    "id": "Tongyi-MAI/Z-Image-Turbo",
    "pipeline_tag": "text-to-image",
    "library_name": "diffusers",
    "tags": ["diffusers", "safetensors", "text-to-image", "en", "license:apache-2.0", "diffusers:ZImagePipeline"],
    "safetensors_parameters": {"F32": 6_154_908_736},
    "siblings": [
        {"name": "README.md", "size": 13_684},
        {"name": "assets/showcase.jpg", "size": 6_433_750},
        {"name": "model_index.json", "size": 467},
        {"name": "text_encoder/model-00001-of-00003.safetensors", "size": 3_957_900_840},
        {"name": "text_encoder/model-00002-of-00003.safetensors", "size": 3_987_450_520},
        {"name": "text_encoder/model-00003-of-00003.safetensors", "size": 99_630_640},
        {"name": "transformer/diffusion_pytorch_model-00001-of-00003.safetensors", "size": 9_973_693_184},
        {"name": "transformer/diffusion_pytorch_model-00002-of-00003.safetensors", "size": 9_973_714_824},
        {"name": "transformer/diffusion_pytorch_model-00003-of-00003.safetensors", "size": 4_672_282_880},
        {"name": "vae/diffusion_pytorch_model.safetensors", "size": 167_666_902},
    ],
}


def test_a_picture_model_is_found_for_what_it_is() -> None:
    card = hub.card_from_dict(Z_IMAGE)
    assert card["format"] == "diffusers"
    assert card["task"] == "images"
    assert card["capabilities"] == ["images"]
    assert card["pipeline"] == "ZImagePipeline"
    assert card["runnable"] is True
    assert card["license"] == "apache-2.0"
    # Served in bfloat16: the float32 transformer counts at half its files.
    assert card["weights_bytes"] == pytest.approx(20.5e9, rel=0.01)


def test_a_lora_has_no_pipeline_to_run() -> None:
    lora = {**Z_IMAGE, "id": "someone/Z-Image-LoRAs",
            "tags": ["diffusers", "text-to-image", "license:apache-2.0"],
            "siblings": [{"name": "style.safetensors", "size": 300_000_000}]}
    card = hub.card_from_dict(lora)
    assert card["task"] == "images"
    assert (card["runnable"], card["not_runnable_reason"]) == (False, "no_pipeline")


def test_a_variant_copy_is_counted_once() -> None:
    names = [
        "unet/diffusion_pytorch_model.safetensors",
        "unet/diffusion_pytorch_model.fp16.safetensors",
        "vae/diffusion_pytorch_model.fp16.safetensors",
        "image.safetensors",
    ]
    assert facts.diffusers_weight_files(names) == [
        "unet/diffusion_pytorch_model.safetensors",
        "vae/diffusion_pytorch_model.fp16.safetensors",
    ]


def test_the_images_search_asks_the_hub_for_whole_pipelines() -> None:
    assert hub.TASK_QUERIES["images"] == ({"pipeline_tag": "text-to-image", "filter": "diffusers"},)


RUNTIME_WITH_PICTURES = compat.RuntimeSupport(
    vllm_version="0.29.0", model_types=frozenset({"qwen3"}), architectures=frozenset({"Qwen3ForCausalLM"}),
    pipelines=frozenset({"ZImagePipeline", "FluxPipeline"}), diffusers_version="0.41.0",
)
RUNTIME_WITHOUT = compat.RuntimeSupport(
    vllm_version="0.29.0", model_types=frozenset({"qwen3"}), architectures=frozenset({"Qwen3ForCausalLM"}),
)


def test_the_runtime_check_reads_the_pipeline() -> None:
    assert compat.check({"pipeline": "ZImagePipeline"}, RUNTIME_WITH_PICTURES).status == compat.RUNS
    newer = compat.check({"pipeline": "QwenImagePipeline"}, RUNTIME_WITH_PICTURES)
    assert (newer.status, newer.code) == (compat.CANNOT_RUN, "pipeline_unknown")
    assert "diffusers 0.41.0" in newer.message
    none = compat.check({"pipeline": "ZImagePipeline"}, RUNTIME_WITHOUT)
    assert (none.status, none.code) == (compat.CANNOT_RUN, "no_image_app")
    # Nothing known about the runtime: nothing to refuse on.
    assert compat.check({"pipeline": "ZImagePipeline"}, None).status == compat.UNKNOWN


def test_a_picture_model_fits_one_share_of_a_gb10() -> None:
    hardware = fit.ClusterHardware(
        environment_id="e", name="dgx-pair", status="ready",
        machines=[fit.Machine(node_id="n", name="spark", gpus=gpus_from_utilization(
            {"gpu": {"total_vram_bytes": GB10_MIB * 1024**2, "free_vram_bytes": GB10_MIB * 1024**2}}))],
    )
    planned = fit.plan(fit.quick_needs(int(20.5e9), single_device=True), hardware)
    assert planned.status == "fits"
    assert planned.tensor_parallel == 1
    assert planned.shareable is True
    assert planned.gpus_per_copy < 0.3


def test_too_large_for_one_device_is_not_split() -> None:
    hardware = fit.ClusterHardware(
        environment_id="e", name="two small cards", status="ready",
        machines=[fit.Machine(node_id="n", name="box", gpus=gpus_from_utilization(
            {"gpu": {"count": 2, "total_vram_bytes": 2 * 16 * 1024**3}}))],
    )
    planned = fit.plan(fit.quick_needs(int(20.5e9), single_device=True), hardware)
    assert planned.status == "too_large"


def test_a_picture_model_gets_no_vllm_settings() -> None:
    assert settings.suggest({"repo_id": Z_IMAGE["id"], "capabilities": ["images"], "max_context": None}, None) == {
        "config": {}, "reasons": {},
    }


def _request(**over: Any) -> HostRequest:
    base: dict[str, Any] = {
        "repo_id": Z_IMAGE["id"], "environment_id": uuid.uuid4(), "name": "z-image-turbo", "alias": "pictures",
        "copies": 1, "gpus_per_copy": 0.2, "images": True,
        "engine_config": {"num_inference_steps": 9, "guidance_scale": 0.0, "max_model_len": 4096},
    }
    base.update(over)
    return HostRequest(**base)


def test_hosting_a_picture_model_writes_an_image_app_spec() -> None:
    spec = build_spec(_request(machine="gpu-02"))
    assert spec["engine"] == {"name": "diffusers", "config": {"num_inference_steps": 9, "guidance_scale": 0.0}}
    assert spec["resources"] == {"replica": {"gpus": 0.2, "machine": "gpu-02"}}
    assert spec["service"]["alias"] == "pictures"
    assert "topology" not in spec

    from faden_backend.services.inference.drivers.ray.compiler import compile_deployment

    doc = compile_deployment(spec_data=spec, model_display_name="Z-Image-Turbo", model_source="remote",
                             hf_repo_id=Z_IMAGE["id"], node_resource="node:10.100.0.1")
    assert doc["image_configs"][0]["deployment_config"]["ray_actor_options"]["num_gpus"] == 0.2


def test_a_picture_model_is_not_split_across_devices() -> None:
    with pytest.raises(HostError, match="one accelerator"):
        build_spec(_request(gpus_per_copy=2))
