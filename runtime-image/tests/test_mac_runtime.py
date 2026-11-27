"""The Mac runtime build helpers: the raise-only mlx-lm patch and the OCI carrier packer.

Pure Python, no Mac needed::

    python -m pytest runtime-image/tests/test_mac_runtime.py
"""

from __future__ import annotations

import importlib.util
import json
import resource
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

MAC = Path(__file__).resolve().parents[1] / "mac"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, MAC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


patcher = _load("patch_mlx_lm_rlimit")
packer = _load("oci_pack")

UPSTREAM = (
    "import resource\n"
    "from huggingface_hub import snapshot_download\n\n"
    "# For large models with lots of files\n"
    "resource.setrlimit(resource.RLIMIT_NOFILE, (2048, 4096))\n\n"
    "from mlx.utils import tree_flatten\n"
)


def _site(tmp_path: Path, text: str) -> Path:
    pkg = tmp_path / "site" / "mlx_lm"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "utils.py").write_text(text)
    (pkg / "__pycache__" / "utils.cpython-312.pyc").write_bytes(b"stale")
    return tmp_path / "site"


def test_patch_replaces_the_lowering_line_and_drops_stale_bytecode(tmp_path: Path) -> None:
    site = _site(tmp_path, UPSTREAM)
    assert patcher.patch(site) == "patched"
    text = (site / "mlx_lm" / "utils.py").read_text()
    assert patcher.ORIGINAL not in text
    assert patcher.MARKER in text
    assert not list((site / "mlx_lm" / "__pycache__").glob("utils.*.pyc"))
    assert patcher.patch(site) == "already"


@pytest.mark.parametrize("text", [UPSTREAM.replace("(2048, 4096)", "(4096, 8192)"), UPSTREAM + patcher.ORIGINAL])
def test_patch_refuses_an_upstream_that_moved(tmp_path: Path, text: str) -> None:
    with pytest.raises(SystemExit):
        patcher.patch(_site(tmp_path, text))


def test_patched_code_never_lowers_either_limit(tmp_path: Path) -> None:
    """Run the replacement in a child with a high soft limit: nothing goes down."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min(hard, 8192) if hard != resource.RLIM_INFINITY else 8192
    code = (
        "import resource, json\n"
        f"resource.setrlimit(resource.RLIMIT_NOFILE, ({target}, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))\n"
        "before = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
        + patcher.REPLACEMENT
        + "print(json.dumps([before, resource.getrlimit(resource.RLIMIT_NOFILE)]))\n"
    )
    before, after = json.loads(subprocess.check_output([sys.executable, "-I", "-c", code]))
    assert after == before


def test_patched_code_raises_a_low_soft_limit(tmp_path: Path) -> None:
    code = (
        "import resource, json\n"
        "resource.setrlimit(resource.RLIMIT_NOFILE, (256, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))\n"
        + patcher.REPLACEMENT
        + "print(json.dumps(resource.getrlimit(resource.RLIMIT_NOFILE)))\n"
    )
    soft, hard = json.loads(subprocess.check_output([sys.executable, "-I", "-c", code]))
    assert soft == (2048 if hard == resource.RLIM_INFINITY else min(2048, hard))
    assert hard == resource.getrlimit(resource.RLIMIT_NOFILE)[1]


def _stage(tmp_path: Path) -> Path:
    rt = tmp_path / "stage" / "runtime"
    (rt / "venv" / "bin").mkdir(parents=True)
    (rt / "venv" / "bin" / "x").write_text("x")
    (rt / "RUNTIME-MANIFEST.json").write_text(json.dumps({"engines": {"vllm": "0.34.0"}}))
    return tmp_path / "stage"


def test_pack_is_a_scratch_carrier_that_cannot_run(tmp_path: Path) -> None:
    out = tmp_path / "img.tar"
    ids = packer.pack(_stage(tmp_path), out, "ghcr.io/fadenstack/mac-runtime-arm64:t")
    with tarfile.open(out) as tar:
        index = json.load(tar.extractfile("index.json"))
        manifest = json.load(tar.extractfile(f"blobs/sha256/{ids['manifest_digest'][7:]}"))
        config = json.load(tar.extractfile(f"blobs/sha256/{ids['config_digest'][7:]}"))
    assert index["manifests"][0]["digest"] == ids["manifest_digest"]
    # Linux Docker's classic store refuses os=darwin; the target lives in labels.
    assert (config["os"], config["architecture"]) == ("linux", "arm64")
    labels = config["config"]["Labels"]
    assert labels["org.fadenstack.runtime.os"] == "darwin"
    assert labels["org.fadenstack.runtime.kind"] == "native-carrier"
    assert "Entrypoint" not in config["config"] and "Cmd" not in config["config"]
    assert config["rootfs"]["diff_ids"] == [ids["layer_digest"]]
    assert manifest["annotations"]["org.fadenstack.runtime.os"] == "darwin"
    assert not (tmp_path / "img.tar.work").exists()


def test_pack_is_reproducible(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    a = packer.pack(stage, tmp_path / "a.tar", "r:t")
    b = packer.pack(stage, tmp_path / "b.tar", "r:t")
    assert a == {**b, "image": a["image"]}
    assert (tmp_path / "a.tar").read_bytes() == (tmp_path / "b.tar").read_bytes()


def test_pack_refuses_a_tree_without_its_manifest(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "runtime" / "RUNTIME-MANIFEST.json").unlink()
    with pytest.raises(SystemExit):
        packer.pack(stage, tmp_path / "x.tar", "r:t")
