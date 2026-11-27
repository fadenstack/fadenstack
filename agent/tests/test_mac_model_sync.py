"""Model sync on the Mac (track M): store hardening, git-SHA1 check, leftovers, remove_model."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from faden_agent import hf_download, model_puller
from faden_agent.event_buffer import EventBuffer
from faden_agent.model_puller import ModelPullerError, pull_model
from faden_agent.runtime_manager import RuntimeManager, RuntimeManagerError
from faden_agent.state_store import StateStore

DIR = "models--org--tiny"
COMMIT = "a" * 40


@pytest.fixture()
def mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_puller, "_darwin", lambda: True)


def _git_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


def _manifest(blobs: dict[str, bytes], links: dict[str, str]) -> dict[str, Any]:
    return {
        "model_id": "m1", "model_dir_name": DIR, "hf_repo_id": "org/tiny",
        "blobs": [{"hash": h, "size": len(d)} for h, d in blobs.items()],
        "refs": [{"name": "main", "commit": COMMIT}],
        "snapshots": [{"commit": COMMIT, "links": [{"path": p, "blob_hash": h} for p, h in links.items()]}],
        "total_size": sum(len(d) for d in blobs.values()),
    }


async def _pull(store: Path, manifest: dict[str, Any], blobs: dict[str, bytes], *, serve: dict[str, bytes] | None = None) -> None:
    served = serve if serve is not None else blobs

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=served[name]) if name in served else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://b") as client:
        await pull_model(client=client, credential="t", model_sync=manifest, model_store_root=str(store))


@pytest.mark.asyncio()
async def test_layout_is_hf_cache_with_relative_links_and_unterminated_ref(tmp_path: Path, mac: None) -> None:
    data = b'{"a": 1}'
    h = hashlib.sha256(data).hexdigest()
    await _pull(tmp_path, _manifest({h: data}, {"config.json": h}), {h: data})
    link = tmp_path / DIR / "snapshots" / COMMIT / "config.json"
    assert link.is_symlink() and not os.path.isabs(os.readlink(link))
    assert os.readlink(link) == f"../../blobs/{h}"
    assert (tmp_path / DIR / "refs" / "main").read_bytes() == COMMIT.encode()


@pytest.mark.asyncio()
async def test_git_sha1_blob_is_verified_on_download(tmp_path: Path, mac: None) -> None:
    data = b"small text file\n"
    h = _git_sha1(data)
    await _pull(tmp_path, _manifest({h: data}, {"README.md": h}), {h: data})
    assert (tmp_path / DIR / "blobs" / h).read_bytes() == data
    bad = tmp_path / "other"
    with pytest.raises(ModelPullerError, match="Hash mismatch"):
        await _pull(bad, _manifest({h: data}, {"README.md": h}), {h: data}, serve={h: b"tampered cont!!\n"})
    assert not (bad / DIR / "blobs" / h).exists()


@pytest.mark.asyncio()
async def test_git_sha1_cached_blob_with_wrong_content_is_fetched_again(tmp_path: Path, mac: None) -> None:
    data = b"hello world!\n"
    h = _git_sha1(data)
    blobs = tmp_path / DIR / "blobs"
    blobs.mkdir(parents=True)
    (blobs / h).write_bytes(b"x" * len(data))  # right size, wrong content
    await _pull(tmp_path, _manifest({h: data}, {"a.txt": h}), {h: data})
    assert (blobs / h).read_bytes() == data


@pytest.mark.asyncio()
async def test_git_sha1_is_not_checked_off_a_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_puller, "_darwin", lambda: False)
    data = b"small\n"
    h = _git_sha1(data)
    await _pull(tmp_path, _manifest({h: b"small\n"}, {"a": h}), {h: data}, serve={h: b"other\n"})  # same size: no check, as 0.2.12
    assert (tmp_path / DIR / "blobs" / h).read_bytes() == b"other\n"


@pytest.mark.asyncio()
async def test_preflight_refuses_when_the_disk_is_too_small(tmp_path: Path, mac: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_puller.shutil, "disk_usage", lambda _p: SimpleNamespace(total=10, used=0, free=1 << 30))
    data = b"x" * 10
    h = hashlib.sha256(data).hexdigest()
    manifest = _manifest({h: data}, {"w": h})
    manifest["blobs"][0]["size"] = 5 << 30
    with pytest.raises(ModelPullerError, match="Not enough disk space.*short by"):
        await _pull(tmp_path, manifest, {h: data})
    assert not (tmp_path / DIR / "blobs" / h).exists()


@pytest.mark.asyncio()
async def test_preflight_passes_with_room_and_counts_only_what_is_missing(tmp_path: Path, mac: None, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"y" * 100
    h = hashlib.sha256(data).hexdigest()
    seen: list[int] = []
    monkeypatch.setattr(model_puller, "preflight_space", lambda _s, n: seen.append(n))
    blobs = tmp_path / DIR / "blobs"
    blobs.mkdir(parents=True)
    (blobs / f"{h}.part").write_bytes(data[:40])
    await _pull(tmp_path, _manifest({h: data}, {"w": h}), {h: data})
    assert seen == [60]


@pytest.mark.asyncio()
async def test_case_colliding_paths_are_refused_on_a_case_insensitive_store(tmp_path: Path, mac: None, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"z"
    h = hashlib.sha256(data).hexdigest()
    manifest = _manifest({h: data}, {"A.txt": h, "a.txt": h})
    monkeypatch.setattr(model_puller, "_store_is_case_insensitive", lambda _s: True)
    with pytest.raises(ModelPullerError, match="differ only in case"):
        await _pull(tmp_path, manifest, {h: data})
    monkeypatch.setattr(model_puller, "_store_is_case_insensitive", lambda _s: False)
    # a case-sensitive volume is not refused at the manifest check (the link step is not reached here)
    model_puller._refuse_case_collisions(tmp_path, manifest["snapshots"])


@pytest.mark.asyncio()
async def test_crash_leftovers_are_cleaned_but_wanted_partials_resume(tmp_path: Path, mac: None) -> None:
    data = b"0123456789" * 10
    h = hashlib.sha256(data).hexdigest()
    base = tmp_path / DIR
    (base / "blobs").mkdir(parents=True)
    (base / "snapshots" / f".{COMMIT}.tmp-dead").mkdir(parents=True)
    (base / "blobs" / ("f" * 64 + ".part")).write_bytes(b"orphan")
    (base / "blobs" / f"{h}.part").write_bytes(data[:30])
    ranges: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ranges.append(request.headers.get("Range"))
        return httpx.Response(206, content=data[30:], headers={"Content-Range": f"bytes 30-{len(data) - 1}/{len(data)}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://b") as client:
        await pull_model(client=client, credential="t", model_sync=_manifest({h: data}, {"w": h}), model_store_root=str(tmp_path))
    assert ranges == ["bytes=30-"]
    assert (base / "blobs" / h).read_bytes() == data
    assert not (base / "snapshots" / f".{COMMIT}.tmp-dead").exists()
    assert not (base / "blobs" / ("f" * 64 + ".part")).exists()


@pytest.mark.asyncio()
async def test_leftovers_are_left_alone_off_a_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_puller, "_darwin", lambda: False)
    data = b"q"
    h = hashlib.sha256(data).hexdigest()
    orphan = tmp_path / DIR / "blobs" / ("f" * 64 + ".part")
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    await _pull(tmp_path, _manifest({h: data}, {"w": h}), {h: data})
    assert orphan.exists()


@pytest.mark.asyncio()
async def test_blobs_are_fsynced_before_they_are_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_puller, "_darwin", lambda: False)  # all platforms
    synced: list[int] = []
    real = os.fsync
    monkeypatch.setattr(model_puller.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    data = b"fsync me"
    h = hashlib.sha256(data).hexdigest()
    await _pull(tmp_path, _manifest({h: data}, {"w": h}), {h: data})
    assert synced


# ── store_problem, xet cache, store root ──────────────────────────────────────

def test_store_problem_on_a_mac_has_no_chown_and_no_user_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "store"
    store.mkdir()
    store.chmod(0o500)
    monkeypatch.setattr(hf_download.sys, "platform", "darwin")
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(hf_download.os, "access", lambda *_a: False)
    text = hf_download.store_problem(str(store))
    store.chmod(0o700)
    assert text and "chown" not in text and "sudo faden-agent start" in text and "None" not in text


def test_store_problem_on_linux_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hf_download.sys, "platform", "linux")
    monkeypatch.setenv("USER", "dgx")
    monkeypatch.setattr(hf_download.os, "access", lambda *_a: False)
    text = hf_download.store_problem(str(tmp_path))
    assert text and f"sudo chown -R dgx: {tmp_path}" in text


def test_xet_cache_goes_under_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_XET_CACHE", raising=False)
    hf_download._keep_xet_cache_in_store(str(tmp_path))
    assert os.environ.pop("HF_XET_CACHE") == str(tmp_path / ".cache" / "xet")


# ── remove_model ──────────────────────────────────────────────────────────────

def _manager(store: Path) -> RuntimeManager:
    return RuntimeManager(
        runtime=MagicMock(), state_store=StateStore(store.parent / "state.json"), events=EventBuffer(),
        advertise_host="10.0.0.1", model_store_root=str(store),
    )


def _populate(store: Path, name: str = DIR) -> Path:
    base = store / name
    (base / "blobs").mkdir(parents=True)
    (base / "snapshots" / COMMIT).mkdir(parents=True)
    (base / "blobs" / "h1").write_bytes(b"x" * 1000)
    (base / "snapshots" / COMMIT / "config.json").symlink_to("../../blobs/h1")
    (store / ".locks" / name).mkdir(parents=True)
    return base


@pytest.mark.asyncio()
async def test_remove_model_deletes_the_model_and_is_idempotent(tmp_path: Path) -> None:
    store = tmp_path / "models"
    base = _populate(store)
    other = _populate(store, "models--org--other")
    mgr = _manager(store)
    result = await mgr.remove_model({"hf_repo_id": "org/tiny"}, in_use=lambda: ([], 0))
    assert result == {"removed": True, "model_dir_name": DIR, "freed_bytes": 1000}
    assert not base.exists() and not (store / ".locks" / DIR).exists() and other.exists()
    again = await mgr.remove_model({"model_dir_name": DIR})
    assert again["removed"] is False


@pytest.mark.asyncio()
async def test_remove_model_refused_while_a_worker_uses_the_snapshot(tmp_path: Path) -> None:
    store = tmp_path / "models"
    base = _populate(store)
    snap = str(base / "snapshots" / COMMIT)
    mgr = _manager(store)
    with pytest.raises(RuntimeManagerError, match="model_in_use"):
        await mgr.remove_model({"model_dir_name": DIR}, in_use=lambda: ([snap], 0))
    with pytest.raises(RuntimeManagerError, match="model_in_use"):
        await mgr.remove_model({"model_dir_name": DIR}, in_use=lambda: ([], 1))
    assert base.exists()
    await mgr.remove_model({"model_dir_name": DIR}, in_use=lambda: ([str(store / "models--org--other" / "snapshots" / COMMIT)], 0))
    assert not base.exists()


@pytest.mark.asyncio()
@pytest.mark.parametrize("name", ["../escape", "/etc", "not-a-model", "models--a/../../x", ""])
async def test_remove_model_refuses_unsafe_names(tmp_path: Path, name: str) -> None:
    store = tmp_path / "models"
    store.mkdir()
    (tmp_path / "victim").mkdir()
    with pytest.raises(RuntimeManagerError):
        await _manager(store).remove_model({"model_dir_name": name})
    assert (tmp_path / "victim").exists()


@pytest.mark.asyncio()
async def test_remove_model_never_follows_symlinks_out_of_the_store(tmp_path: Path) -> None:
    store = tmp_path / "models"
    base = _populate(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (base / "blobs" / "evil").symlink_to(outside)
    await _manager(store).remove_model({"model_dir_name": DIR})
    assert (outside / "keep.txt").read_text() == "keep"
    # the model folder itself being a link out of the store is refused, and its target untouched
    (store / "models--org--link").symlink_to(outside)
    with pytest.raises(RuntimeManagerError, match="outside"):
        await _manager(store).remove_model({"model_dir_name": "models--org--link"})
    assert (outside / "keep.txt").exists()


# ── dispatcher and capability gate ───────────────────────────────────────────

@pytest.mark.asyncio()
async def test_remove_model_command_is_unsupported_without_the_native_runtime(tmp_path: Path) -> None:
    from faden_agent.dispatcher import CommandDispatcher
    from faden_agent.policy_guard import PolicyGuard

    d = CommandDispatcher(
        state_store=StateStore(tmp_path / "s.json"), runtime_manager=MagicMock(), policy_guard=PolicyGuard(image_allowlist=None),
        events=EventBuffer(),
    )
    async def _noop(_p: Any) -> None:
        return None

    result = await d.handle({"id": "c1", "command_type": "remove_model", "payload": {"hf_repo_id": "org/tiny"}}, _noop)
    assert result.get("success") is False


def test_model_remove_is_advertised_only_with_a_native_manager() -> None:
    import inspect

    from faden_agent.service import NodeAgentService

    src = inspect.getsource(NodeAgentService._start_native)
    assert 'static_capabilities["model_remove"] = 1' in src
    assert src.index('"model_remove"') > src.index("manager.capabilities()")


def test_native_manager_reports_snapshots_in_use(tmp_path: Path) -> None:
    from faden_agent.native.manager import NativeRuntimeManager

    mgr = SimpleNamespace(_scan=lambda: ([
        {"state": "ready", "model": {"snapshot_path": "/m/a"}},
        {"state": "starting", "model": {"snapshot_path": "/m/b"}},
        {"state": "stopped", "model": {"snapshot_path": "/m/c"}},
        {"state": "failed", "model": {"snapshot_path": "/m/d"}},
    ], [Path("bad.json")]))
    assert NativeRuntimeManager.model_paths_in_use(mgr) == (["/m/a", "/m/b"], 1)  # type: ignore[arg-type]
