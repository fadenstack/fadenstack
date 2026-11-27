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
    with pytest.raises(RuntimeManagerError, match="symlink"):
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
    ], [Path("bad.json")]), _leader=lambda _r: None)
    assert NativeRuntimeManager.model_paths_in_use(mgr) == (["/m/a", "/m/b"], 1)  # type: ignore[arg-type]


# ── review fixes: case variants, locks, aliases, store choice ────────────────

CASE_INSENSITIVE = not (Path("/tmp/.Case-Probe-x").exists() or True) and False


def test_lock_and_keys_fold_case_and_var_aliases_on_a_mac(tmp_path: Path, mac: None) -> None:
    a = tmp_path / "models--Org--x"
    b = tmp_path / "MODELS--org--X"
    assert model_puller.canonical_key(a) == model_puller.canonical_key(b)
    assert model_puller._lock_for_model_dir(a) is model_puller._lock_for_model_dir(b)
    assert model_puller.is_within(b / "snapshots" / COMMIT, a)
    assert not model_puller.is_within(tmp_path / "models--Org--xy", a)
    assert model_puller.canonical_key("/var/db/x") == model_puller.canonical_key("/private/var/db/x")


def test_keys_stay_case_sensitive_off_a_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_puller, "_darwin", lambda: False)
    assert model_puller.canonical_key(tmp_path / "A") != model_puller.canonical_key(tmp_path / "a")


@pytest.mark.asyncio()
async def test_remove_is_refused_for_a_case_variant_of_a_model_in_use(tmp_path: Path, mac: None) -> None:
    store = tmp_path / "models"
    base = _populate(store, "models--Org--x")
    snap = str(base / "snapshots" / COMMIT)
    with pytest.raises(RuntimeManagerError, match="model_in_use"):
        await _manager(store).remove_model({"model_dir_name": "models--org--x"}, in_use=lambda: ([snap], 0))
    assert base.exists()


@pytest.mark.asyncio()
async def test_remove_is_refused_through_a_var_alias_and_for_relative_paths(tmp_path: Path, mac: None) -> None:
    store = tmp_path / "models"
    base = _populate(store)
    alias = str(base / "snapshots" / COMMIT).replace(str(tmp_path), "/private" + str(tmp_path) if not str(tmp_path).startswith("/private") else str(tmp_path), 1)
    with pytest.raises(RuntimeManagerError, match="model_in_use"):
        await _manager(store).remove_model({"model_dir_name": DIR}, in_use=lambda: ([alias], 0))
    with pytest.raises(RuntimeManagerError, match="model_in_use"):
        await _manager(store).remove_model({"model_dir_name": DIR}, in_use=lambda: (["snapshots/x"], 0))
    assert base.exists()


@pytest.mark.asyncio()
async def test_remove_refuses_in_store_symlinks_nul_and_a_symlinked_locks_dir(tmp_path: Path) -> None:
    store = tmp_path / "models"
    _populate(store, "models--org--b")
    (store / "models--org--a").symlink_to("models--org--b")
    with pytest.raises(RuntimeManagerError, match="symlink"):
        await _manager(store).remove_model({"model_dir_name": "models--org--a"})
    assert (store / "models--org--b" / "blobs" / "h1").exists()
    with pytest.raises(RuntimeManagerError):
        await _manager(store).remove_model({"model_dir_name": "models--org\x00x"})
    # a symlinked .locks is not followed
    victim = tmp_path / "victim"
    (victim / "models--org--b").mkdir(parents=True)
    import shutil

    shutil.rmtree(store / ".locks")
    (store / ".locks").symlink_to(victim)
    await _manager(store).remove_model({"model_dir_name": "models--org--b"})
    assert (victim / "models--org--b").exists()


@pytest.mark.asyncio()
async def test_concurrent_syncs_of_case_variants_do_not_clean_each_others_partials(tmp_path: Path, mac: None) -> None:
    import asyncio

    data_a, data_b = b"a" * 64, b"b" * 64
    ha, hb = hashlib.sha256(data_a).hexdigest(), hashlib.sha256(data_b).hexdigest()
    (tmp_path / "models--Org--x" / "blobs").mkdir(parents=True)
    (tmp_path / "models--Org--x" / "blobs" / f"{hb}.part").write_bytes(data_b[:10])
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        await asyncio.sleep(0.05)
        gate.set()
        return httpx.Response(200, content={ha: data_a, hb: data_b}[name])

    ma = _manifest({ha: data_a}, {"a": ha}) | {"model_dir_name": "models--Org--x"}
    mb = _manifest({hb: data_b}, {"b": hb}) | {"model_dir_name": "models--org--x"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://b") as client:
        await asyncio.gather(*(
            pull_model(client=client, credential="t", model_sync=m, model_store_root=str(tmp_path)) for m in (ma, mb)
        ))
    # serialised by one lock: both finished, neither failed on a file the other removed


def test_the_native_start_takes_the_models_lock() -> None:
    import inspect

    from faden_agent.native.manager import NativeRuntimeManager

    assert "_lock_for_model_dir(model_dir)" in inspect.getsource(NativeRuntimeManager.start_native_worker)


def test_in_use_counts_every_live_state(tmp_path: Path) -> None:
    from faden_agent.native.manager import NativeRuntimeManager

    recs = [{"state": s, "model": {"snapshot_path": f"/m/{s}"}} for s in ("ready", "starting", "stopping", "weird", "stopped", "failed")]
    mgr = SimpleNamespace(_scan=lambda: (recs, []), _leader=lambda r: object() if r["state"] == "failed" else None)
    paths, bad = NativeRuntimeManager.model_paths_in_use(mgr)  # type: ignore[arg-type]
    assert sorted(paths) == ["/m/failed", "/m/ready", "/m/starting", "/m/stopping", "/m/weird"] and bad == 0
    mgr2 = SimpleNamespace(_scan=lambda: (recs, []), _leader=lambda r: None)
    assert "/m/failed" not in NativeRuntimeManager.model_paths_in_use(mgr2)[0]  # type: ignore[arg-type]


# ── configured store on a Mac ────────────────────────────────────────────────

def _layout(tmp_path: Path) -> Any:
    return SimpleNamespace(models=tmp_path / "var-db" / "models", state_dir=tmp_path / "var-db")


def test_a_configured_external_store_is_honoured_when_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import pwd

    from faden_agent import __main__ as m

    ext = tmp_path / "vol" / "models"
    ext.mkdir(parents=True)
    ext.chmod(0o700)
    monkeypatch.setattr(pwd, "getpwnam", lambda _n: SimpleNamespace(pw_uid=os.getuid()))
    assert m._darwin_model_store(str(ext), _layout(tmp_path)) == str(ext.resolve())
    ext.chmod(0o755)
    assert m._darwin_model_store(str(ext), _layout(tmp_path)) == str(_layout(tmp_path).models)
    assert "WARNING" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["/srv/faden/models", "relative/dir", "/Users/someone/models", "/System/x", "/nonexistent/zzz", "/Library/Fadenstack/m", "/Library/x", "/usr/local/m", "/bin/m", "/sbin/m", "/etc/m", "/private/etc/m", "/Applications/m"])
def test_an_unsafe_configured_store_is_replaced_loudly(tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: str) -> None:
    from faden_agent import __main__ as m

    assert m._darwin_model_store(bad, _layout(tmp_path)) == str(_layout(tmp_path).models)
    assert "WARNING" in capsys.readouterr().out


def test_an_unset_store_is_the_layout_default(tmp_path: Path) -> None:
    from faden_agent import __main__ as m

    assert m._darwin_model_store(None, _layout(tmp_path)) == str(_layout(tmp_path).models)


def test_a_store_inside_the_state_dir_outside_models_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import pwd

    from faden_agent import __main__ as m

    layout = _layout(tmp_path)
    monkeypatch.setattr(pwd, "getpwnam", lambda _n: SimpleNamespace(pw_uid=os.getuid()))
    native = layout.state_dir / "native"
    native.mkdir(parents=True)
    native.chmod(0o700)
    assert m._darwin_model_store(str(native), layout) == str(layout.models)
    assert "WARNING" in capsys.readouterr().out
    sub = layout.models / "extra"
    sub.mkdir(parents=True)
    sub.chmod(0o700)
    assert m._darwin_model_store(str(sub), layout) == str(sub.resolve())  # under models/: allowed


@pytest.mark.asyncio()
async def test_a_stop_is_not_blocked_by_a_sync_holding_the_model_lock(tmp_path: Path) -> None:
    """A start waits on the model's lock (a long sync) without holding the manager lock."""
    import asyncio

    from faden_agent.native.manager import NativeRuntimeManager

    root = tmp_path / "models"
    snap = root / DIR / "snapshots" / COMMIT
    snap.mkdir(parents=True)
    spec = SimpleNamespace(snapshot=snap.resolve())
    mgr = SimpleNamespace(
        _model_root=root, _sandbox_config_error=None, _sandbox_wanted=False, _sandbox_available=True,
        _lock=asyncio.Lock(),
    )
    from faden_agent.native import manager as nm

    orig = nm.workers.parse_spec
    nm.workers.parse_spec = lambda payload, model_root: spec  # type: ignore[assignment]
    try:
        sync_lock = model_puller._lock_for_model_dir(root / DIR)
        await sync_lock.acquire()  # a sync of this model is running
        start = asyncio.create_task(NativeRuntimeManager.start_native_worker(mgr, {}))  # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        assert not start.done()
        # The manager lock is free: a stop (which takes it) completes meanwhile.
        await asyncio.wait_for(mgr._lock.acquire(), timeout=1)
        mgr._lock.release()
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        sync_lock.release()
    finally:
        nm.workers.parse_spec = orig  # type: ignore[assignment]
