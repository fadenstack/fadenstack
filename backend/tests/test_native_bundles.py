"""Native runtime bundles (a Mac running vLLM-Metal) beside the Ray catalogue.

What has to hold:
- a native bundle is matched only through its explicit darwin/apple target,
  never as a container image for an arm64 Linux machine (DGX GB10 is arm64 too);
- a Mac is never handed a Linux container runtime;
- every Ray rule stays as it was: a Ray bundle still needs ray/cuda versions,
  a Linux machine that reports no vendor or OS still matches.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from faden_backend.services.inference import bundles
from faden_backend.services.inference.bundles import (
    AcceleratorSpec,
    BundleValidationError,
    CompatibilityMatrix,
    NativeRuntimeSpec,
    RuntimeBundleManifest,
    RuntimeBundleRegistry,
    TargetArchitecture,
    native_bundle_from_manifest,
)

D = "sha256:" + "a" * 64


def native_manifest(**over: Any) -> dict[str, Any]:
    manifest = {
        "runtime_id": "faden-native-vllm-metal-darwin-arm64-vllm0.34.0",
        "flavor": "darwin-arm64",
        "native": {
            "oci_image": "ghcr.io/fadenstack/mac-runtime-arm64:vllm0.34.0-metal0.34.0-1",
            "digest": "sha256:" + "1" * 64,
            "config_digest": "sha256:" + "2" * 64,
            "layer_digest": "sha256:" + "3" * 64,
            "rootfs_digest": "sha256:" + "4" * 64,
            "size_bytes": 900_000_000,
            "interpreter": {"version": "3.12.12", "abi": "cp312", "path": "python/cpython-3.12.12/bin/python3.12"},
            "engines": {"vllm": "0.34.0", "vllm_metal": "0.34.0", "mlx": "0.32.4", "mlx_lm": "0.32.3.dev12+g4cfc647"},
            "patches": {"mlx_lm_fd_cap_raise_only": True},
        },
        "stack_components": {"python": "3.12.12", "vllm": "0.34.0", "torch": "2.16.0.dev20261013"},
        "certification": {"overall_status": "passed", "checks": [{"name": "smoke", "status": "PASS"}]},
    }
    manifest.update(over)
    return manifest


def darwin_target() -> TargetArchitecture:
    flavor = bundles._FLAVORS["darwin-arm64"]  # noqa: SLF001
    return flavor["target_architecture"]


def native_bundle(**over: Any) -> RuntimeBundleManifest:
    return native_bundle_from_manifest(
        native_manifest(**over),
        bundle_id="faden-native-vllm-metal-darwin-arm64-vllm0.34.0",
        display_name="Mac runtime",
        driver="standalone",
        target_architecture=darwin_target(),
    )


def node(**caps: Any) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), agent_id="m", host="10.0.0.9", capabilities_json=caps)


MAC = dict(os="macOS-26.1-arm64-arm-64bit", machine="arm64", gpu_vendor="apple", native_runtime=1, model_tls_native=1)
OLD_MAC = dict(MAC, os="macOS-15.6-arm64-arm-64bit")
GB10 = dict(
    os="Linux-6.11.0-1016-nvidia-aarch64-with-glibc2.39", machine="aarch64", gpu_vendor="nvidia",
    gpu={"vendor": "nvidia", "devices": [{"compute_capability": "12.1"}]},
)
X86 = dict(os="Linux-6.8.0-x86_64-with-glibc2.39", machine="x86_64", gpu_vendor="nvidia",
           gpu={"vendor": "nvidia", "devices": [{"compute_capability": "8.6"}]})


@pytest.fixture()
def registry() -> RuntimeBundleRegistry:
    reg = RuntimeBundleRegistry()
    reg.register_bundle(native_bundle())
    return reg


# ── the manifest ─────────────────────────────────────────────────────────


def test_a_native_bundle_carries_its_runtime_and_no_container() -> None:
    bundle = native_bundle()
    assert bundle.container is None
    assert bundle.native is not None
    assert bundle.artifact_ref == "ghcr.io/fadenstack/mac-runtime-arm64:vllm0.34.0-metal0.34.0-1"
    assert bundle.artifact_digest == "sha256:" + "2" * 64
    assert bundle.native.repository == "ghcr.io/fadenstack/mac-runtime-arm64"
    assert bundle.native.tag == "vllm0.34.0-metal0.34.0-1"
    assert bundle.native.patches.mlx_lm_fd_cap_raise_only
    assert bundle.compatibility_matrix.ray_version == ""
    assert bundle.compatibility_matrix.cuda_version == ""
    assert bundle.certification.status == "passed"
    RuntimeBundleRegistry.validate_bundle(bundle)


def test_a_runtime_without_the_fd_patch_is_not_certified_passed() -> None:
    manifest = native_manifest()
    manifest["native"]["patches"] = {"mlx_lm_fd_cap_raise_only": False}
    bundle = native_bundle_from_manifest(
        manifest, bundle_id="x", display_name="x", driver="standalone", target_architecture=darwin_target(),
    )
    assert bundle.certification.status == "partial"


def test_exactly_one_artifact() -> None:
    container = bundles.ContainerSpec(image="ghcr.io/x/y:1", digest=D)
    native = native_bundle().native
    matrix = CompatibilityMatrix(vllm_version="0.34.0")
    target = darwin_target()
    with pytest.raises(ValidationError, match="exactly one"):
        RuntimeBundleManifest(bundle_id="b", display_name="b", target_architecture=target,
                              container=container, native=native, compatibility_matrix=matrix)
    with pytest.raises(ValidationError, match="exactly one"):
        RuntimeBundleManifest(bundle_id="b", display_name="b", compatibility_matrix=matrix)


def test_a_native_bundle_must_state_its_os_and_vendor() -> None:
    native = native_bundle().native
    matrix = CompatibilityMatrix(vllm_version="0.34.0")
    for target in (
        TargetArchitecture(cpu="arm64", accelerator=AcceleratorSpec(vendor="apple")),  # no os
        TargetArchitecture(cpu="arm64", os="darwin", accelerator=AcceleratorSpec()),  # no vendor
        TargetArchitecture(cpu="arm64", os="darwin"),  # no accelerator
    ):
        with pytest.raises(ValidationError, match="os and accelerator vendor"):
            RuntimeBundleManifest(bundle_id="b", display_name="b", driver="standalone",
                                  target_architecture=target, native=native, compatibility_matrix=matrix)


def test_digests_are_checked() -> None:
    raw = native_manifest()["native"]
    with pytest.raises(ValidationError):
        NativeRuntimeSpec.model_validate({**raw, "layer_digest": "sha256:nope"})


def test_a_ray_bundle_still_needs_its_ray_and_cuda_versions() -> None:
    """Refused when it is read, not only when it is registered (review #8)."""
    with pytest.raises(ValidationError, match="ray_version, cuda_version"):
        RuntimeBundleManifest(
            bundle_id="r", display_name="r",
            container=bundles.ContainerSpec(image="ghcr.io/x/y:1", digest=D),
            compatibility_matrix=CompatibilityMatrix(vllm_version="0.31.0"),
        )
    with pytest.raises(ValidationError, match="cuda_version"):
        RuntimeBundleManifest(
            bundle_id="r", display_name="r",
            container=bundles.ContainerSpec(image="ghcr.io/x/y:1", digest=D),
            compatibility_matrix=CompatibilityMatrix(vllm_version="0.31.0", ray_version="2.58.0"),
        )


def test_the_shipped_ray_catalogue_is_unchanged() -> None:
    """The four minted Ray runtimes still load, as containers, under the Ray driver."""
    catalogue = bundles._catalogue()  # noqa: SLF001
    ray = {k: v for k, v in catalogue.items() if v.driver == "ray"}
    assert len(ray) >= 4
    for bundle in ray.values():
        assert bundle.container is not None and bundle.native is None
        assert bundle.target_architecture.os == "linux"
        assert bundle.target_architecture.accelerator.vendor == "nvidia"
        assert bundle.compatibility_matrix.ray_version and bundle.compatibility_matrix.cuda_version
        RuntimeBundleRegistry.validate_bundle(bundle)


# ── matching machines ────────────────────────────────────────────────────


def test_a_mac_gets_the_native_runtime(registry: RuntimeBundleRegistry) -> None:
    mac = node(**MAC)
    chosen = registry.resolve_for_node(mac, driver="standalone")
    assert chosen is not None and chosen.native is not None


def test_a_mac_never_gets_a_ray_container(registry: RuntimeBundleRegistry) -> None:
    mac = node(**MAC)
    assert registry.resolve_for_node(mac, driver="ray") is None
    for bundle in registry.bundles_for_driver("ray"):
        ok, reason = registry.validate_node_compatibility(bundle, mac)
        assert not ok, bundle.bundle_id
        assert "darwin" in reason or "vendor" in reason


def test_gb10_never_gets_the_native_carrier(registry: RuntimeBundleRegistry) -> None:
    """GB10 is arm64 like a Mac: the carrier is matched by darwin/apple, never by architecture."""
    gb10 = node(**GB10)
    native = registry.bundles_for_driver("standalone")
    assert native
    for bundle in native:
        ok, reason = registry.validate_node_compatibility(bundle, gb10)
        assert not ok
        assert "darwin" in reason
    assert registry.resolve_for_node(gb10, driver="standalone") is None
    chosen = registry.resolve_for_node(gb10, driver="ray")
    assert chosen is not None and chosen.container is not None
    assert "gb10" in chosen.bundle_id


def test_an_x86_machine_resolves_as_before(registry: RuntimeBundleRegistry) -> None:
    x86 = node(**X86)
    chosen = registry.resolve_for_node(x86, driver="ray")
    assert chosen is not None and "x86_64" in chosen.bundle_id
    assert registry.resolve_for_node(x86, driver="standalone") is None


def test_a_linux_machine_that_reports_no_vendor_still_matches_ray(registry: RuntimeBundleRegistry) -> None:
    """The nvidia fallback is kept for a machine that says it runs Linux."""
    linux = node(os="Linux-6.8.0-x86_64-with-glibc2.39", machine="x86_64", gpu={"devices": [{"compute_capability": "8.6"}]})
    assert registry.resolve_for_node(linux, driver="ray") is not None
    assert registry.resolve_for_node(linux, driver="standalone") is None


def test_a_machine_with_a_reported_vendor_and_no_os_still_matches_ray(registry: RuntimeBundleRegistry) -> None:
    x86 = node(machine="x86_64", gpu={"vendor": "nvidia", "devices": [{"compute_capability": "8.6"}]})
    assert registry.resolve_for_node(x86, driver="ray") is not None


@pytest.mark.parametrize("machine", ["arm64", "aarch64", "x86_64"])
def test_a_machine_that_reports_neither_os_nor_vendor_gets_no_runtime(
    registry: RuntimeBundleRegistry, machine: str,
) -> None:
    """Review #4: a Mac whose GPU collector failed reports ``arm64`` alone; it must not get the GB10 container."""
    blind = node(machine=machine)
    assert registry.resolve_for_node(blind, driver="ray") is None
    assert registry.resolve_for_node(blind, driver="standalone") is None
    gb10 = next(b for b in registry.bundles_for_driver("ray") if "gb10" in b.bundle_id)
    ok, reason = registry.validate_node_compatibility(gb10, blind)
    assert not ok and "neither a Linux OS nor its GPU vendor" in reason


def test_a_windows_machine_running_linux_containers_keeps_matching_ray(registry: RuntimeBundleRegistry) -> None:
    win = node(**dict(X86, os="Windows-11-10.0.26100-SP0"))
    assert registry.resolve_for_node(win, driver="ray") is not None


def test_a_mac_below_the_minimum_os_is_refused(registry: RuntimeBundleRegistry) -> None:
    old = node(**OLD_MAC)
    bundle = registry.bundles_for_driver("standalone")[0]
    ok, reason = registry.validate_node_compatibility(bundle, old)
    assert not ok and "26.0" in reason and "15.6" in reason


def test_a_mac_that_has_not_reported_its_gpu_is_refused(registry: RuntimeBundleRegistry) -> None:
    mac = node(**{k: v for k, v in MAC.items() if k != "gpu_vendor"})
    bundle = registry.bundles_for_driver("standalone")[0]
    ok, reason = registry.validate_node_compatibility(bundle, mac)
    assert not ok and "vendor" in reason


@pytest.mark.parametrize(
    ("reported", "name", "version"),
    [
        ("macOS-26.1-arm64-arm-64bit", "darwin", "26.1"),
        ("Darwin-25.1.0-arm64-arm-64bit", "darwin", "25.1.0"),
        ("Linux-6.8.0-x86_64-with-glibc2.39", "linux", "6.8.0"),
        ("", None, None),
    ],
)
def test_reading_the_reported_os(reported: str, name: str | None, version: str | None) -> None:
    assert bundles.node_os_name({"os": reported}) == name
    assert bundles.node_os_version({"os": reported}) == version
