"""A certification run's verdict goes into the manifest of the image it ran -- and no other.

``runtime-image/rebuild_runtime_image.py`` writes the manifest a build mints
and records a certification into it; this catalogue reads both. The two meet
here: what the recorder writes is what ``from_runtime_manifest`` reads.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from faden_backend.services.inference.bundles import RuntimeBundleManifest

_SCRIPT = Path(__file__).resolve().parents[2] / "runtime-image" / "rebuild_runtime_image.py"
LAYERS = ["sha256:" + f"{i:02x}" * 32 for i in range(3)]


@pytest.fixture(scope="module")
def rebuild():
    spec = importlib.util.spec_from_file_location("rebuild_runtime_image", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses resolve their annotations through it
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _manifest(tmp_path: Path, **certification) -> Path:
    path = tmp_path / "runtime-manifest-gb10-nv26.09.json"
    path.write_text(json.dumps({
        "runtime_id": "faden-ray-vllm-gb10-ray2.58-nv26.09",
        "flavor": "gb10",
        "release_tag": "faden/ray-vllm-gb10:ray2.58-nv26.09",
        "image_id": "sha256:" + "ab" * 32,
        "rootfs_layers": LAYERS,
        "stack_components": {"ray": "2.58.0", "vllm": "0.29.0", "cuda": "13.4"},
        "certification": {"hardware_target": "NVIDIA DGX Spark / GB10", "overall_status": "uncertified", **certification},
    }), encoding="utf-8")
    return path


def _report(tmp_path: Path, *, layers=LAYERS, verdict: str | None = "PASSED", partial: bool = False) -> Path:
    checks = [
        {"check": "Two-Node Cluster Formation", "status": "PASS", "detail": "2 nodes online"},
        {"check": "Cluster Prometheus Metrics Export", "status": "PARTIAL" if partial else "PASS", "detail": "head only"},
    ]
    path = tmp_path / "report.json"
    report = {"target": "NVIDIA DGX Spark Two-Node Pair (GB10)", "timestamp": "2026-09-29T21:00:00Z",
              "checks": checks, "image": {"tag": "faden/ray-vllm-gb10:ray2.58-nv26.09",
                                          "rootfs_layers": {"head": layers, "worker": LAYERS}}}
    if verdict:
        report["overall_status"] = verdict
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _catalogued(path: Path) -> RuntimeBundleManifest:
    return RuntimeBundleManifest.from_runtime_manifest(
        json.loads(path.read_text(encoding="utf-8")), bundle_id="x", display_name="x",
    )


def test_a_passed_run_certifies_the_image_it_ran_as_a_preview(rebuild, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    rebuild.record_certification(manifest, _report(tmp_path), channel="preview")

    cert = _catalogued(manifest).certification
    assert (cert.status, cert.channel, cert.checks_passed, cert.checks_total) == ("passed", "preview", 2, 2)
    assert cert.timestamp == "2026-09-29T21:00:00Z"


def test_a_partial_run_reads_partial_with_what_fell_short(rebuild, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    rebuild.record_certification(manifest, _report(tmp_path, verdict="PASSED_WITH_PARTIAL", partial=True))
    cert = _catalogued(manifest).certification
    assert cert.status == "partial"
    assert cert.notes == ["Cluster Prometheus Metrics Export: PARTIAL - head only"]


@pytest.mark.parametrize(
    ("report_kwargs", "says"),
    [({"layers": ["sha256:" + "ff" * 32]}, "the head ran other content"), ({"verdict": None}, "no verdict")],
    ids=["another-build", "no-verdict"],
)
def test_evidence_that_does_not_fit_is_refused(rebuild, tmp_path: Path, report_kwargs: dict, says: str) -> None:
    manifest = _manifest(tmp_path)
    before = manifest.read_text(encoding="utf-8")
    with pytest.raises(rebuild.BuildError, match=says):
        rebuild.record_certification(manifest, _report(tmp_path, **report_kwargs))
    assert manifest.read_text(encoding="utf-8") == before


def test_a_build_starts_uncertified_under_the_name_of_its_base(rebuild, tmp_path: Path, monkeypatch) -> None:
    """The GB10 runtime is named after the Dockerfile's NVIDIA base, so a new base is a new runtime."""
    monkeypatch.setattr(rebuild, "base_image_of", lambda flavor: {"repository": "nvcr.io/nvidia/vllm", "tag": "26.09-py3"})
    base = rebuild._from_ref("Dockerfile").rsplit(":", 1)[-1].split("-")[0]
    flavor = rebuild.FLAVORS["gb10"]
    assert f"nv{base}" in flavor.runtime_id and f"nv{base}" in flavor.manifest and f"nv{base}" in flavor.default_tag
    written = rebuild.write_manifest(
        tmp_path / "m.json", flavor.default_tag, flavor,
        {"image_id": "sha256:" + "ab" * 32, "rootfs_layers": LAYERS, "repo_digest": None},
        {"ray": "2.58.0", "vllm": "0.29.0", "cuda": "13.4"}, {},
    )
    assert (written["runtime_id"], written["flavor"]) == (flavor.runtime_id, "gb10")
    cert = _catalogued(tmp_path / "m.json").certification
    assert (cert.status, cert.channel, cert.checks_total) == ("uncertified", "stable", 0)
