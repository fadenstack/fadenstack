"""Staging a native runtime: digests, relocation, idempotency and hostile archives."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from faden_agent.native import NativeError, NativePaths
from faden_agent.native import stage as st
from tests.native_fakes import (
    build_runtime,
    carrier,
    file_member,
    layer_from_dir,
    layer_from_members,
    link_member,
    special_member,
)


@pytest.fixture()
def paths(tmp_path: Path) -> NativePaths:
    return NativePaths(tmp_path / "native")


def _stage(paths: NativePaths, c, **kw):
    return st.stage_from_tar(
        c.tar, paths=paths, bundle_id="faden-native-vllm-metal", config_digest=c.config_digest,
        layer_digest=c.layer_digest, size_bytes=c.size_bytes, **kw,
    )


@pytest.mark.parametrize("compress", [False, True])
def test_stages_verifies_relocates_and_stamps(tmp_path: Path, paths: NativePaths, compress: bool) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"), compress=compress)
    final = _stage(paths, c)
    assert final == paths.runtime_dir("faden-native-vllm-metal", c.layer_digest)
    cfg = (final / "runtime" / "venv" / "pyvenv.cfg").read_text()
    assert f"home = {final}/runtime/python/bin" in cfg
    assert (final / "runtime" / "venv" / "bin" / "python").resolve() == (
        final / "runtime" / "python" / "bin" / "python3.12"
    ).resolve()
    assert st.read_stamp(final)["layer_digest"] == c.layer_digest
    assert st.is_staged(paths, "faden-native-vllm-metal", c.layer_digest)
    assert not list(paths.bundle_dir("faden-native-vllm-metal").glob(".staging-*"))


def test_same_digest_again_is_a_no_op(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    final = _stage(paths, c)
    marker = final / "runtime" / "touched"
    marker.write_text("kept")
    c.tar.unlink()  # not even read
    assert _stage(paths, c) == final
    assert marker.read_text() == "kept"


def test_an_unstamped_leftover_is_replaced(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    final = paths.runtime_dir("faden-native-vllm-metal", c.layer_digest)
    (final / "runtime").mkdir(parents=True)
    (final / "runtime" / "junk").write_text("half")
    _stage(paths, c)
    assert not (final / "runtime" / "junk").exists() and st.read_stamp(final)


def test_a_different_image_id_is_only_logged(tmp_path: Path, paths: NativePaths) -> None:
    # The image Id differs between Docker's classic and containerd stores; the content is the pin.
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    c.config_digest = "sha256:" + "0" * 64
    assert st.read_stamp(_stage(paths, c, rootfs_digest=st.rootfs_digest_of([c.layer_digest])))


def test_wrong_rootfs_digest(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    with pytest.raises(NativeError) as err:
        _stage(paths, c, rootfs_digest="sha256:" + "3" * 64)
    assert err.value.error_code == "digest_mismatch"


def test_rootfs_digest_matches_the_backends() -> None:
    import hashlib

    ids = ["sha256:" + "a" * 64]
    assert st.rootfs_digest_of(ids) == "sha256:" + hashlib.sha256(ids[0].encode()).hexdigest()


def test_interpreter_path_must_agree(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    with pytest.raises(NativeError) as err:
        _stage(paths, c, interpreter_path="python/bin/python3.13")
    assert err.value.error_code == "invalid_runtime"
    assert _stage(paths, c, interpreter_path="python/bin/python3.12")


def test_no_rewrite_when_the_bundle_says_so(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    final = _stage(paths, c, pyvenv_home_rewrite=False)
    assert "home = /build/somewhere/python/bin" in (final / "runtime" / "venv" / "pyvenv.cfg").read_text()


def test_wrong_layer_digest(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"), diff_id="sha256:" + "1" * 64)
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code == "digest_mismatch"
    assert not paths.runtime_dir("faden-native-vllm-metal", c.layer_digest).exists()


def test_a_linux_image_is_refused(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"), os_name="linux")
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code == "invalid_image"


def test_runtime_without_the_fd_patch_is_refused(tmp_path: Path, paths: NativePaths) -> None:
    build_runtime(tmp_path / "src", manifest={
        "kind": "python-venv", "interpreter": {"path": "python/bin/python3.12"},
        "engines": {"vllm": "0.34.0"}, "patches": {},
    })
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code == "invalid_runtime"


ATTACKS = {
    "absolute": [file_member("/etc/evil")],
    "dotdot": [file_member("runtime/../../evil")],
    "outside_runtime": [file_member("other/file")],
    "symlink_absolute": [link_member("runtime/ln", "/etc/passwd")],
    "symlink_escape": [link_member("runtime/ln", "../../../outside")],
    "symlink_then_write_through": [link_member("runtime/ln", "../.."), file_member("runtime/ln/evil")],
    "hardlink_outside": [link_member("runtime/hl", "/etc/passwd", hard=True)],
    "hardlink_unknown": [link_member("runtime/hl", "runtime/nothere", hard=True)],
    "device": [special_member("runtime/dev", tarfile.CHRTYPE)],
    "block": [special_member("runtime/blk", tarfile.BLKTYPE)],
    "fifo": [special_member("runtime/fifo", tarfile.FIFOTYPE)],
    "setuid": [file_member("runtime/suid", mode=0o4755)],
    "setgid": [file_member("runtime/sgid", mode=0o2755)],
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_hostile_layers_are_refused(tmp_path: Path, paths: NativePaths, name: str) -> None:
    c = carrier(tmp_path / "img.tar", layer_from_members(ATTACKS[name]))
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code == "unsafe_archive", err.value
    assert not (tmp_path / "evil").exists() and not (tmp_path / "outside").exists()
    assert not list(paths.bundle_dir("faden-native-vllm-metal").glob(".staging-*"))
    assert not paths.runtime_dir("faden-native-vllm-metal", c.layer_digest).exists()


def test_more_than_declared_is_refused(tmp_path: Path, paths: NativePaths) -> None:
    c = carrier(tmp_path / "img.tar", layer_from_members([file_member("runtime/big", b"0" * 3_000_000)]))
    with pytest.raises(NativeError) as err:
        _stage(paths, c, unpacked_size_bytes=100_000)
    assert err.value.error_code == "unsafe_archive"


def test_a_gzip_bomb_stops_at_the_ratio(tmp_path: Path, paths: NativePaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "MIN_LIMIT", 0)
    layer = layer_from_members([file_member("runtime/zeros", b"\0" * 5_000_000)])
    c = carrier(tmp_path / "img.tar", layer, compress=True)
    assert c.size_bytes * st.MAX_RATIO < len(layer)
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code == "unsafe_archive"


def test_too_many_members(tmp_path: Path, paths: NativePaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "MAX_MEMBERS", 3)
    c = carrier(tmp_path / "img.tar", layer_from_members([file_member(f"runtime/f{i}") for i in range(5)]))
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code == "unsafe_archive"


def test_in_tree_links_are_fine() -> None:
    files = {"runtime/python/bin/python3.12"}
    assert st.check_member(link_member("runtime/venv/bin/python", "../../python/bin/python3.12")[0], files)
    assert st.check_member(link_member("runtime/venv/bin/py2", "runtime/python/bin/python3.12", hard=True)[0], files)


def test_a_symlink_chain_ends_inside_runtime(tmp_path: Path) -> None:
    # Lexically every hop stays inside runtime/; on disk c -> b/.. would be the
    # staging dir itself. The data filter normalises it; the realpath check is
    # the second layer.
    import io
    import os

    members = [file_member("runtime/x"), link_member("runtime/b", "."), link_member("runtime/c", "b/..")]
    assert st.check_member(members[2][0], set())  # the lexical check alone lets it through
    dest = tmp_path / "dest"
    dest.mkdir()
    st.extract_layer(io.BytesIO(layer_from_members(members)), dest, limit=1 << 20)
    root = os.path.realpath(dest / "runtime")
    for name in ("b", "c"):
        assert os.path.realpath(dest / "runtime" / name).startswith(root)


def test_the_realpath_check_refuses_a_link_out_of_runtime(tmp_path: Path) -> None:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "up").symlink_to("..")
    with pytest.raises(NativeError) as err:
        st._check_link(tmp_path, "runtime/up")
    assert err.value.error_code == "unsafe_archive"


def test_an_exact_unpacked_size_passes(tmp_path: Path, paths: NativePaths) -> None:
    src = tmp_path / "src"
    build_runtime(src)
    total = sum(p.stat().st_size for p in (src / "runtime").rglob("*") if p.is_file() and not p.is_symlink())
    c = carrier(tmp_path / "img.tar", layer_from_dir(src))
    assert _stage(paths, c, unpacked_size_bytes=total)


# ── the layer as docker save hands it over ───────────────────────


def test_a_gzip_layer_from_the_containerd_store_is_verified_over_its_decompressed_tar(
    tmp_path: Path, paths: NativePaths
) -> None:
    import hashlib

    build_runtime(tmp_path / "src")
    plain = layer_from_dir(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", plain, compress=True)
    # The diff_id is the plain tar's; the blob in the image tar is the gzip.
    assert c.layer_digest == "sha256:" + hashlib.sha256(plain).hexdigest()
    with tarfile.open(c.tar) as outer:
        _config, layer = st.read_image(outer)
        assert outer.extractfile(layer).read(2) == b"\x1f\x8b"
    final = _stage(paths, c, rootfs_digest=st.rootfs_digest_of([c.layer_digest]), unpacked_size_bytes=len(plain))
    assert st.read_stamp(final)["layer_digest"] == c.layer_digest


def test_a_gzip_layer_unpacking_past_its_declared_size_is_refused(tmp_path: Path, paths: NativePaths) -> None:
    # The bomb cap with the backend's figure: declared unpacked size + 1 % + 1 MiB.
    layer = layer_from_members([file_member("runtime/zeros", b"\0" * 8_000_000)])
    c = carrier(tmp_path / "img.tar", layer, compress=True)
    with pytest.raises(NativeError) as err:
        _stage(paths, c, unpacked_size_bytes=1_000_000)
    assert err.value.error_code == "unsafe_archive"


def test_a_zstd_layer_is_refused_clearly(tmp_path: Path, paths: NativePaths) -> None:
    plain = layer_from_members([file_member("runtime/x")])
    fake_zstd = b"\x28\xb5\x2f\xfd" + b"\0" * 64
    c = carrier(tmp_path / "img.tar", plain)
    # Replace the layer blob with a zstd-looking one (the diff_id stays the plain tar's).
    with tarfile.open(c.tar) as outer:
        members = [(m, outer.extractfile(m).read()) for m in outer.getmembers()]
    import io

    with tarfile.open(c.tar, "w") as out:
        for m, data in members:
            if data == plain:
                data = fake_zstd
                m.size = len(data)
            out.addfile(m, io.BytesIO(data))
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code == "invalid_image" and "zstd" in str(err.value)


@pytest.mark.parametrize("damage", ["truncated_gzip", "garbage_layer", "garbage_image"])
def test_damaged_downloads_fail_cleanly(tmp_path: Path, paths: NativePaths, damage: str) -> None:
    import gzip
    import io

    plain = layer_from_members([file_member("runtime/x", b"y" * 100_000)])
    c = carrier(tmp_path / "img.tar", plain, compress=True)
    if damage == "garbage_image":
        c.tar.write_bytes(b"not a tar at all" * 100)
    else:
        with tarfile.open(c.tar) as outer:
            members = [(m, outer.extractfile(m).read()) for m in outer.getmembers()]
        with tarfile.open(c.tar, "w") as out:
            for m, data in members:
                if data[:2] == b"\x1f\x8b":
                    data = data[: len(data) // 2] if damage == "truncated_gzip" else gzip.compress(b"\x01" * 4096)
                    m.size = len(data)
                out.addfile(m, io.BytesIO(data))
    with pytest.raises(NativeError) as err:
        _stage(paths, c)
    assert err.value.error_code in {"invalid_image", "digest_mismatch"}
    assert not list(paths.bundle_dir("faden-native-vllm-metal").glob(".staging-*"))
