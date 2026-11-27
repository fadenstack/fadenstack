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


# ── the dependency mirror ────────────────────────────────────────────────

fetcher = _load("fetch_deps")


def _pins() -> dict:
    return json.loads((MAC / "deps.json").read_text())


def test_the_shipped_lock_builds_from_pypi_and_the_mirror_alone() -> None:
    assert fetcher.check_lock((MAC / "requirements-lock.txt").read_text(), _pins()) == []


def test_every_mirrored_wheel_is_pinned_and_named_safely() -> None:
    pins = _pins()
    assert pins["release_tag"].startswith("mac-runtime-deps-")
    for wheel in pins["wheels"]:
        assert len(wheel["sha256"] or "") == 64, wheel["name"]
        assert "+" not in wheel["asset"]
        assert fetcher.asset_url(pins, wheel).startswith(
            f"https://github.com/fadenstack/fadenstack/releases/download/{pins['release_tag']}/"
        )


@pytest.mark.parametrize(
    ("line", "words"),
    [
        ("mlx-lm @ git+https://github.com/ml-explore/mlx-lm@4cfc647", "neither a PyPI pin"),
        ("torch==2.16.0.dev20261013", "nightly"),
        ("vllm @ https://github.com/vllm-project/vllm/releases/download/v0.34.0/x.whl", "neither a PyPI pin"),
        ("--extra-index-url https://download.pytorch.org/whl/nightly/cpu", "options are not allowed"),
        ("foo @ file://@WHEELS@/foo-1-py3-none-any.whl", "not pinned in deps.json"),
    ],
)
def test_the_lock_refuses_anything_from_outside_the_mirror(line: str, words: str) -> None:
    lock = (MAC / "requirements-lock.txt").read_text() + line + "\n"
    problems = fetcher.check_lock(lock, _pins())
    assert any(words in p for p in problems), problems


def test_a_pin_the_lock_does_not_use_is_reported() -> None:
    lock = "\n".join(l for l in (MAC / "requirements-lock.txt").read_text().splitlines() if not l.startswith("vllm-metal"))
    assert any("pins vllm-metal but the lock does not use it" in p for p in fetcher.check_lock(lock, _pins()))


def test_a_wheel_is_taken_only_with_its_pinned_sha256(tmp_path: Path) -> None:
    good = tmp_path / "src" / "a-1-py3-none-any.whl"
    good.parent.mkdir()
    good.write_bytes(b"wheel")
    pins = {
        "repository": "fadenstack/fadenstack", "release_tag": "mac-runtime-deps-1",
        "wheels": [{"name": "a", "file": good.name, "asset": good.name, "sha256": fetcher.sha256(good)}],
    }
    fetcher.fetch(tmp_path / "out", source=good.parent, pins=pins)
    assert (tmp_path / "out" / good.name).read_bytes() == b"wheel"

    good.write_bytes(b"tampered")
    (tmp_path / "out" / good.name).unlink()
    with pytest.raises(SystemExit, match="refusing it"):
        fetcher.fetch(tmp_path / "out", source=good.parent, pins=pins)
    assert not (tmp_path / "out" / good.name).exists()


def test_a_lock_line_without_a_hash_fails_the_check() -> None:
    lock = (MAC / "requirements-lock.txt").read_text() + "six==1.17.0\n"
    assert any("six==1.17.0 has no --hash" in p for p in fetcher.check_lock(lock, _pins()))


def test_a_mirrored_wheel_with_another_hash_fails_the_check() -> None:
    text = (MAC / "requirements-lock.txt").read_text()
    pin = next(w for w in _pins()["wheels"] if w["name"] == "torch")["sha256"]
    tampered = text.replace(pin, "0" * 64, 1)
    assert any("is not its deps.json pin" in p for p in fetcher.check_lock(tampered, _pins()))


def test_every_requirement_in_the_shipped_lock_is_hashed() -> None:
    entries = fetcher.parse_lock((MAC / "requirements-lock.txt").read_text())
    assert len(entries) > 150
    assert all(hashes for _, _, hashes in entries)
    assert all(len(h) == 64 for _, _, hashes in entries for h in hashes)


def test_the_build_installs_with_require_hashes_and_no_other_index() -> None:
    script = (MAC / "build_mac_runtime.sh").read_text()
    assert "--require-hashes" in script
    assert "--no-build" in script
    assert "--index-url https://pypi.org/simple" in script
    for banned in ("extra-index-url", "unsafe-best-match", "download.pytorch.org", "git+"):
        assert banned not in script, banned



def test_the_minted_entry_records_the_unpacked_size(tmp_path: Path) -> None:
    minter = _load("mint_manifest")
    (tmp_path / "digests.json").write_text(json.dumps({
        "image": "ghcr.io/fadenstack/mac-runtime-arm64:t", "manifest_digest": "sha256:" + "1" * 64,
        "config_digest": "sha256:" + "2" * 64, "layer_digest": "sha256:" + "3" * 64,
        "size_bytes": 10, "unpacked_size_bytes": 40,
    }))
    (tmp_path / "RUNTIME-MANIFEST.json").write_text(json.dumps({
        "interpreter": {"version": "3.12.12", "abi": "cp312", "path": "python/x/bin/python3.12"},
        "entrypoint": ["venv/bin/python"], "engines": {"vllm": "0.34.0+cpu", "vllm_metal": "0.34.0"},
        "stack": {"python": "3.12.12"}, "patches": {"mlx_lm_fd_cap_raise_only": True},
    }))
    (tmp_path / "smoke.json").write_text(json.dumps({
        "metal_available": True, "rlimit_nofile_before": [256, 1024], "rlimit_nofile_after": [256, 1024],
    }))
    assert minter.mint(tmp_path, status="uncertified", channel="stable")["native"]["unpacked_size_bytes"] == 40


def test_the_shipped_entry_records_the_unpacked_size() -> None:
    entry = json.loads((MAC.parent / "runtime-manifest-darwin-arm64-vllm-metal0.34.0.json").read_text())
    assert entry["native"]["unpacked_size_bytes"] == 2_158_704_640
