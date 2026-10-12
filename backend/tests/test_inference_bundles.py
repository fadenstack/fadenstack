"""Unit tests for Runtime Bundle Manifest manager and platform tuning injection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.inference.bundles import (  # noqa: PLC0415
    ContainerSpec,
    RuntimeBundleManifest,
    RuntimeBundleRegistry,
    _manifest_paths,
    compute_rootfs_digest,
    default_bundle_registry,
)

_SPARK = InfraNode(agent_id="spark", host="10.250.10.49", capabilities_json={"machine": "aarch64", "gpu_vendor": "nvidia"})


def _gb10() -> RuntimeBundleManifest:
    """The runtime a DGX Spark gets unless its cluster chose another."""
    bundle = default_bundle_registry.resolve_for_node(_SPARK)
    assert bundle is not None
    return bundle


def _manifests() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in _manifest_paths()]


def test_certified_dgx_spark_bundle_valid() -> None:
    """The DGX Spark's default runtime is a certified, stable one with the GB10 tuning."""
    bundle = _gb10()
    assert bundle.certification.status == "passed"
    assert bundle.certification.channel == "stable"
    assert bundle.target_architecture.cpu == "aarch64"
    assert "12.1" in bundle.target_architecture.accelerator.compute_capabilities
    assert bundle.container.digest is not None
    assert bundle.container.digest.startswith("sha256:")

    # Crucial platform tuning for GB10 unified memory
    assert bundle.platform_tuning.ray.env.get("RAY_memory_monitor_refresh_ms") == "0"
    # NCCL_DEBUG is a diagnostic, not a runtime default: opt-in only.
    assert "NCCL_DEBUG" not in bundle.platform_tuning.nccl.env
    assert bundle.platform_tuning.diagnostics["NCCL_DEBUG"] == "INFO"


def test_certified_bundle_identity_matches_the_deployed_image() -> None:
    """The catalogue entry is the manifest the image build wrote -- nothing else.

    This used to assert a literal digest, which made it a third hand-kept copy
    of a fact the build already records: the catalogue dict, this test and the
    manifest file each held an image id, and they drifted apart. The catalogue
    went on pinning an image that no longer existed anywhere, and every cluster
    start was refused by the integrity check -- correctly -- 303 times.

    So assert the derivation instead. Whatever the last build produced, the
    catalogue must pin exactly that, and derive its content identity from the
    same layers.
    """
    for manifest in _manifests():
        bundle = default_bundle_registry.get_bundle(manifest["runtime_id"])
        assert bundle is not None, manifest["runtime_id"]

        assert bundle.container.image == manifest["release_tag"]
        assert bundle.container.digest == manifest["image_id"]
        if manifest.get("rootfs_layers"):
            assert bundle.container.rootfs_digest == compute_rootfs_digest(list(manifest["rootfs_layers"]))

        # The stack is read from the helper inside the image. Ray is pinned:
        # it refuses to form a cluster across versions, so every runtime of
        # every flavor moves together.
        matrix = bundle.compatibility_matrix
        assert matrix.ray_version == "2.58.0"
        assert matrix.vllm_version == manifest["stack_components"]["vllm"]


def test_every_runtime_is_shipped_as_one_of_three_at_most_per_flavor() -> None:
    """The newest, the one before it, one to go back to: runtime-image/README.md."""
    per_flavor: dict[str, int] = {}
    for manifest in _manifests():
        per_flavor[manifest["flavor"]] = per_flavor.get(manifest["flavor"], 0) + 1
    assert per_flavor and max(per_flavor.values()) <= 3, per_flavor


def test_the_catalogue_is_not_transcribed_beside_the_manifest() -> None:
    """There must be no second, hand-kept copy for the two to disagree with."""
    from faden_backend.services.inference import bundles

    assert not hasattr(bundles, "_CERTIFIED_DGX_SPARK_RUNTIME_MANIFEST")


def test_a_build_time_manifest_records_nccl() -> None:
    """NCCL's version needs the library, not a GPU.

    The in-image helper only asked when a GPU was visible, and a build never
    has one -- so every manifest written at build time recorded nccl as None,
    and the catalogue quietly lost a version it used to show.
    """
    assert _gb10().compatibility_matrix.nccl_version


def test_the_default_runtime_carries_the_run_that_certified_it() -> None:
    """Evidence from the hardware, recorded against the image that ran.

    ``rebuild_runtime_image.py --record-certification`` refuses a report whose
    layers are not the manifest's (``test_runtime_certification_record.py``),
    and a rebuild starts from "uncertified" again.
    """
    cert = _gb10().certification
    assert cert.checks_total > 0
    assert cert.checks_passed == cert.checks_total
    assert cert.timestamp


def test_rootfs_digest_is_canonical_and_order_sensitive() -> None:
    """The agent recomputes this independently; the rule has to be exact."""
    layers = ["sha256:" + "aa" * 32, "sha256:" + "bb" * 32]
    digest = compute_rootfs_digest(layers)
    assert digest.startswith("sha256:")
    assert compute_rootfs_digest(layers) == digest
    # Layer order is part of the identity: two images with the same layers in a
    # different order are not the same image.
    assert compute_rootfs_digest(list(reversed(layers))) != digest


def test_container_requirements_are_semantic_not_cli_flags() -> None:
    """Section 4B: semantic requirements, never raw Docker CLI strings."""
    req = _gb10().container.requirements
    assert req.network_mode == "host"
    assert req.ipc_mode == "host"
    assert req.gpus == "all"
    assert "/dev/infiniband" in req.devices
    for value in (*req.devices, *req.capabilities, req.network_mode, req.ipc_mode, req.gpus):
        assert not str(value).startswith("-"), f"{value!r} looks like a CLI flag"


def test_bundle_digest_is_mandatory_and_checked() -> None:
    """A bundle that cannot be pinned must not be constructible."""
    with pytest.raises(ValidationError):
        ContainerSpec(image="faden/ray-vllm-gb10:ray2.58-vllm0.29.0")
    with pytest.raises(ValidationError):
        ContainerSpec(image="faden/ray-vllm-gb10:ray2.58-vllm0.29.0", digest="0.16.0")


def test_bundle_generated_from_runtime_manifest() -> None:
    """Bundle identity is generated from the build artifact, never hand-written."""
    manifest = {
        "release_tag": "example/img:v1",
        "image_id": "sha256:" + "ab" * 32,
        "stack_components": {"ray": "2.58.0", "vllm": "9.9.9", "cuda": "13.4"},
        "certification": {
            "overall_status": "PASSED",
            "hardware_target": "Example",
            "checks": [
                {"name": "a", "status": "PASS", "detail": "ok"},
                {"name": "b", "status": "PASS", "detail": "scraped head, worker timed out"},
            ],
        },
    }
    built = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert built.container.image == "example/img:v1"
    assert built.container.digest == "sha256:" + "ab" * 32
    assert built.compatibility_matrix.vllm_version == "9.9.9"
    assert built.certification.checks_passed == 2

    # A report that claims PASSED while a check did not pass is downgraded.
    manifest["certification"]["checks"][1]["status"] = "PARTIAL"
    partial = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert partial.certification.status == "partial"
    assert partial.certification.checks_passed == 1
    assert partial.certification.notes


def test_content_identity_is_derived_from_layers_not_transcribed() -> None:
    """A manifest carrying ``rootfs_layers`` yields the digest by computation.

    This is the fix for the image drift: the build emits the layer list and the
    catalog derives identity from it, so there is no hand-copied digest to
    disagree with the artifact.  ``rebuild_runtime_image.py`` deliberately
    writes no ``rootfs_digest`` for this reason.
    """
    layers = ["sha256:" + f"{index:02x}" * 32 for index in range(3)]
    manifest = {
        "release_tag": "example/img:v1",
        "image_id": "sha256:" + "ab" * 32,
        "rootfs_layers": layers,
        "stack_components": {"ray": "2.58.0", "vllm": "9.9.9", "cuda": "13.4"},
    }
    built = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert built.container.rootfs_digest == compute_rootfs_digest(layers)

    # An explicit digest still wins, so an older manifest keeps working.
    manifest["rootfs_digest"] = "sha256:" + "cd" * 32
    pinned = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert pinned.container.rootfs_digest == "sha256:" + "cd" * 32


def test_bundle_yaml_roundtrip(tmp_path: Path) -> None:
    """Verify bundle serialization and loading from YAML manifest."""
    yaml_file = tmp_path / "bundle.yaml"
    data = _gb10().model_dump()
    yaml_file.write_text(yaml.dump(data), encoding="utf-8")

    registry = RuntimeBundleRegistry()
    loaded = registry.load_from_yaml(yaml_file)

    assert loaded.bundle_id == _gb10().bundle_id
    assert loaded.container.image == _gb10().container.image
    assert registry.get_bundle(loaded.bundle_id) is not None


def test_node_compatibility_validation() -> None:
    """Test validating node capabilities against bundle requirements."""
    registry = RuntimeBundleRegistry()
    bundle = _gb10()

    # 1. Compatible node
    good_node = InfraNode(
        agent_id="gpu-02",
        host="10.250.10.49",
        capabilities_json={
            "gpu": {
                "vendor": "nvidia",
                "devices": [{"name": "NVIDIA GB10", "compute_capability": "12.1"}],
            }
        },
    )
    ok, reason = registry.validate_node_compatibility(bundle, good_node)
    assert ok is True
    assert "fully compatible" in reason

    # 2. Incompatible vendor
    amd_node = InfraNode(
        agent_id="amd-node",
        host="10.250.10.99",
        capabilities_json={"gpu": {"vendor": "amd"}},
    )
    ok_amd, reason_amd = registry.validate_node_compatibility(bundle, amd_node)
    assert ok_amd is False
    assert "Incompatible GPU vendor" in reason_amd

    # 3. Incompatible compute capability
    old_node = InfraNode(
        agent_id="v100-node",
        host="10.250.10.98",
        capabilities_json={
            "gpu": {
                "vendor": "nvidia",
                "devices": [{"name": "NVIDIA V100", "compute_capability": "7.0"}],
            }
        },
    )
    ok_old, reason_old = registry.validate_node_compatibility(bundle, old_node)
    assert ok_old is False
    assert "Compute capability mismatch" in reason_old


def test_inject_platform_tuning() -> None:
    """Test platform tuning injection into runtime environment."""
    registry = RuntimeBundleRegistry()
    bundle = _gb10()

    initial_env = {"VLLM_HOST_IP": "10.100.0.1"}
    injected = registry.inject_platform_tuning(bundle, env_vars=initial_env)

    assert injected["VLLM_HOST_IP"] == "10.100.0.1"
    assert injected["RAY_memory_monitor_refresh_ms"] == "0"
    assert injected["NCCL_IB_RETRY_CNT"] == "7"
    # Verbose per-rank tracing is not a runtime default.
    assert "NCCL_DEBUG" not in injected

    opted_in = registry.inject_platform_tuning(
        bundle, env_vars=initial_env, diagnostics=True,
    )
    assert opted_in["NCCL_DEBUG"] == "INFO"


def test_container_launch_spec_carries_semantic_requirements() -> None:
    """The agent launch contract passes requirements through, not flags."""
    registry = RuntimeBundleRegistry()
    spec = registry.container_launch_spec(
        _gb10(),
        name="faden-ray-runtime",
        env={"VLLM_HOST_IP": "10.100.0.1"},
    )
    assert spec["image"] == _gb10().container.image
    assert spec["digest"] == _gb10().container.digest
    assert spec["rootfs_digest"] == _gb10().container.rootfs_digest
    assert spec["requirements"]["network_mode"] == "host"
    assert spec["requirements"]["ipc_mode"] == "host"
    assert spec["env"]["VLLM_HOST_IP"] == "10.100.0.1"
    assert any(m["container_path"] == "/models" for m in spec["mounts"])



# ── resolution and node-supplied mounts ──────────────────────────────────


def _node(agent_id: str, **caps: object) -> InfraNode:
    return InfraNode(agent_id=agent_id, host="10.0.0.1", capabilities_json=dict(caps))


def test_resolution_follows_the_machine_not_a_pin() -> None:
    """Each node gets the image built for its own platform."""
    registry = RuntimeBundleRegistry()

    dgx = _node("spark", machine="aarch64", gpu_vendor="nvidia", gpu_count=1)
    workstation = _node("box", machine="x86_64", gpu_vendor="nvidia", gpu_count=1)

    assert registry.resolve_for_node(dgx).target_architecture.cpu == "aarch64"
    assert registry.resolve_for_node(workstation).target_architecture.cpu == "x86_64"


def test_a_node_that_has_not_reported_its_platform_resolves_to_nothing() -> None:
    """Silence is not a match.

    ``validate_node_compatibility`` is permissive -- an unreported field is
    not a reason something cannot work -- so without this an unknown machine
    matched every bundle and took whichever sorted first, which is how an
    aarch64 image would be sent to a machine nobody had inventoried.
    """
    registry = RuntimeBundleRegistry()
    assert registry.resolve_for_node(_node("unknown")) is None
    assert registry.resolve_for_node(_node("blank", machine="  ")) is None


def test_a_generic_bundle_takes_its_mounts_from_the_node() -> None:
    """The image says which container paths it needs; the node says where."""
    registry = RuntimeBundleRegistry()
    bundle = registry.resolve_for_node(_node("box", machine="x86_64", gpu_vendor="nvidia"))
    assert bundle is not None
    # The point of a generic bundle: it names no host path, because it has
    # never seen the machine.
    assert bundle.container.mounts == []

    node = _node(
        "box",
        machine="x86_64",
        gpu_vendor="nvidia",
        paths={"model_store": "/home/op/.cache/huggingface", "ray_session": "/var/lib/faden/ray"},
    )
    spec = registry.container_launch_spec(bundle, name="faden-ray-runtime", node=node)
    by_target = {m["container_path"]: m for m in spec["mounts"]}
    assert by_target["/models"]["host_path"] == "/home/op/.cache/huggingface"
    assert by_target["/models"]["mode"] == "ro"
    assert by_target["/tmp/ray"]["host_path"] == "/var/lib/faden/ray"
    assert by_target["/tmp/ray"]["mode"] == "rw"


def test_the_nodes_hf_cache_is_mounted_where_hugging_face_looks_and_only_when_it_has_one() -> None:
    registry = RuntimeBundleRegistry()
    for machine, bundle in (("aarch64", _gb10()), ("x86_64", None)):
        bundle = bundle or registry.resolve_for_node(_node("box", machine="x86_64", gpu_vendor="nvidia"))
        # No flavor names a host path of its own for the cache.
        assert not any("huggingface" in m.host_path for m in bundle.container.mounts)
        paths = {"model_store": "/srv/m", "ray_session": "/r"}
        without = registry.container_launch_spec(
            bundle, name="n", node=_node("a", machine=machine, gpu_vendor="nvidia", paths=paths)
        )
        assert "/root/.cache/huggingface/hub" not in {m["container_path"] for m in without["mounts"]}
        withit = registry.container_launch_spec(
            bundle,
            name="n",
            node=_node("a", machine=machine, gpu_vendor="nvidia", paths={**paths, "hf_cache": "/data/me/hub"}),
        )
        by_target = {m["container_path"]: m for m in withit["mounts"]}
        assert by_target["/root/.cache/huggingface/hub"]["host_path"] == "/data/me/hub"
        assert by_target["/root/.cache/huggingface/hub"]["mode"] == "ro"


def test_a_bundle_that_declares_a_mount_keeps_it() -> None:
    """A bundle certified for one machine's layout is not overridden."""
    registry = RuntimeBundleRegistry()
    node = _node(
        "spark",
        machine="aarch64",
        gpu_vendor="nvidia",
        paths={"model_store": "/somewhere/else", "ray_session": "/elsewhere"},
    )
    spec = registry.container_launch_spec(
        _gb10(), name="faden-ray-runtime", node=node
    )
    by_target = {m["container_path"]: m["host_path"] for m in spec["mounts"]}
    assert by_target["/models"] == "/srv/faden/models"
    assert by_target["/tmp/ray"] == "/var/lib/faden/ray"


def test_a_node_with_no_reported_paths_gets_no_invented_ones() -> None:
    """Better an absent mount than one pointing at a guess."""
    registry = RuntimeBundleRegistry()
    bundle = registry.resolve_for_node(_node("box", machine="x86_64", gpu_vendor="nvidia"))
    node = _node("box", machine="x86_64", gpu_vendor="nvidia")
    spec = registry.container_launch_spec(bundle, name="faden-ray-runtime", node=node)
    assert spec["mounts"] == []
