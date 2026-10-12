"""A machine downloads a model from the Hub itself, and says whether it can reach it.

For a cluster whose machines have their own internet: the release VM would
have downloaded a 132 GB model at 12 MB/s and copied it to both DGX Sparks
over the same link, nine hours, while the Sparks had lines of their own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from faden_agent import hf_download
from faden_agent.event_buffer import EventBuffer
from faden_agent.runtime_manager import HUGGING_FACE, RuntimeManager, RuntimeManagerError
from faden_agent.state_store import StateStore

REPO = "nvidia/Qwen3.8-Flash-Next-NVFP4"
COMMIT = "a1b2c3d4"


class _Runtime:
    async def is_available(self) -> bool:
        return True


def _fake_hub(monkeypatch: pytest.MonkeyPatch, files: dict[str, int], fetched: list[dict[str, Any]]) -> None:
    import huggingface_hub

    class _Api:
        def __init__(self, endpoint: str | None = None, token: str | None = None) -> None:
            self.token = token

        def model_info(self, repo_id: str, revision: str | None = None, files_metadata: bool = False) -> Any:
            return SimpleNamespace(sha=COMMIT, siblings=[SimpleNamespace(rfilename=n, size=s) for n, s in files.items()])

    def _download(repo_id: str, filename: str, *, revision: str, cache_dir: str, token: str | None, endpoint: str) -> str:
        fetched.append({"file": filename, "revision": revision, "token": token})
        snapshot = Path(cache_dir) / f"models--{repo_id.replace('/', '--')}" / "snapshots" / revision
        (snapshot / filename).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / filename).write_bytes(b"x")
        return str(snapshot / filename)

    monkeypatch.setattr(huggingface_hub, "HfApi", _Api)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _download)


def test_it_downloads_what_vllm_reads_at_one_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fetched: list[dict[str, Any]] = []
    files = {
        "model-00001-of-00002.safetensors": 10_000, "model-00002-of-00002.safetensors": 10_000,
        "config.json": 10, "tokenizer.json": 10, "onnx/model.onnx": 50_000, "original/consolidated.pth": 50_000,
    }
    _fake_hub(monkeypatch, files, fetched)
    seen: list[tuple[int, int]] = []

    result = hf_download.download_model(
        hf_repo_id=REPO, revision=None, cache_dir=str(tmp_path), token="hf_secret",
        progress=lambda done, total, _f: seen.append((done, total)),
    )

    assert sorted(f["file"] for f in fetched) == [
        "config.json", "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors", "tokenizer.json",
    ]
    # Every file at the commit "main" named when it started: a push halfway through cannot mix versions.
    assert {f["revision"] for f in fetched} == {COMMIT}
    assert {f["token"] for f in fetched} == {"hf_secret"}
    model_dir = tmp_path / "models--nvidia--Qwen3.8-Flash-Next-NVFP4"
    assert (model_dir / "refs" / "main").read_text(encoding="utf-8") == COMMIT
    assert result["commit"] == COMMIT and result["total_size"] == 20_020
    assert Path(result["snapshot_dir"]).is_dir()
    assert seen[-1] == (20_020, 20_020)


def test_a_file_the_machines_hf_cache_holds_is_linked_not_fetched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fetched: list[dict[str, Any]] = []
    _fake_hub(monkeypatch, {"model.safetensors": 5, "config.json": 2}, fetched)
    hub = tmp_path / "hub"
    mine = hub / f"models--{REPO.replace('/', '--')}"
    (mine / "blobs").mkdir(parents=True)
    (mine / "snapshots" / COMMIT).mkdir(parents=True)
    (mine / "blobs" / "etag1").write_bytes(b"12345")
    os.symlink(os.path.join("..", "..", "blobs", "etag1"), mine / "snapshots" / COMMIT / "model.safetensors")
    (mine / "blobs" / "etag2").write_bytes(b"x")  # config.json is short: half a download
    os.symlink(os.path.join("..", "..", "blobs", "etag2"), mine / "snapshots" / COMMIT / "config.json")

    hf_download.download_model(
        hf_repo_id=REPO, revision=None, cache_dir=str(tmp_path / "store"), token=None, machine_hf_cache=str(hub),
    )

    assert (tmp_path / "store" / mine.name / "blobs" / "etag1").read_bytes() == b"12345"
    assert not (tmp_path / "store" / mine.name / "blobs" / "etag2").exists()


@pytest.mark.asyncio()
async def test_sync_model_from_the_hub_answers_like_a_copy_from_the_server(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    async def downloader(*, model_sync: dict[str, Any], emit_progress: Any = None) -> dict[str, Any]:
        calls.append(model_sync)
        await emit_progress({"message": "Downloading", "progress_pct": 50.0})
        (tmp_path / "models--nvidia--Qwen3.8-Flash-Next-NVFP4" / "snapshots" / COMMIT).mkdir(parents=True)
        return {"commit": COMMIT, "total_size": 123, "files": 4, "model_dir_name": "models--nvidia--Qwen3.8-Flash-Next-NVFP4"}

    manager = RuntimeManager(
        runtime=_Runtime(),  # type: ignore[arg-type]
        state_store=StateStore(tmp_path / "state.json"), events=EventBuffer(), advertise_host="10.250.10.71",
        model_store_root=str(tmp_path), model_downloader=downloader,
    )
    progress: list[dict[str, Any]] = []

    async def emit(p: dict[str, Any]) -> None:
        progress.append(p)

    result = await manager.sync_model(
        {"model_sync": {"model_id": "m1", "hf_repo_id": REPO, "source": HUGGING_FACE, "revision": "main"}},
        emit_progress=emit,
    )

    assert calls and calls[0]["hf_repo_id"] == REPO
    assert result["synced"] is True and result["source"] == HUGGING_FACE
    assert result["revision"] == COMMIT and result["total_size"] == 123
    assert result["root_path"].replace("\\", "/").endswith(f"models--nvidia--Qwen3.8-Flash-Next-NVFP4/snapshots/{COMMIT}")
    assert progress == [{"message": "Downloading", "progress_pct": 50.0}]


@pytest.mark.asyncio()
async def test_a_refusal_from_the_server_is_said_as_such(tmp_path: Path) -> None:
    async def downloader(**_: Any) -> dict[str, Any]:
        raise PermissionError("This machine's cluster does not download models itself.")

    manager = RuntimeManager(
        runtime=_Runtime(),  # type: ignore[arg-type]
        state_store=StateStore(tmp_path / "state.json"), events=EventBuffer(), advertise_host="10.0.0.1",
        model_store_root=str(tmp_path), model_downloader=downloader,
    )
    with pytest.raises(RuntimeManagerError, match="does not download models itself"):
        await manager.sync_model({"model_sync": {"model_id": "m1", "hf_repo_id": REPO, "source": HUGGING_FACE}})


@pytest.mark.asyncio()
async def test_an_agent_without_a_downloader_says_so(tmp_path: Path) -> None:
    manager = RuntimeManager(
        runtime=_Runtime(),  # type: ignore[arg-type]
        state_store=StateStore(tmp_path / "state.json"), events=EventBuffer(), advertise_host="10.0.0.1",
        model_store_root=str(tmp_path),
    )
    with pytest.raises(RuntimeManagerError, match="cannot download models"):
        await manager.sync_model({"model_sync": {"model_id": "m1", "hf_repo_id": REPO, "source": HUGGING_FACE}})


# -- can it reach the Hub? ------------------------------------------------------


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(hf_download.httpx, "AsyncClient", factory)


@pytest.mark.asyncio()
async def test_a_machine_that_reaches_the_hub_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda request: httpx.Response(200, json=[{"id": "gpt2"}]))
    result = await hf_download.check_internet({})
    assert result["reachable"] is True and result["status_code"] == 200
    assert result["url"].startswith("https://huggingface.co/api/models")


@pytest.mark.asyncio()
async def test_a_machine_behind_a_closed_firewall_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    _patch_client(monkeypatch, refuse)
    result = await hf_download.check_internet({"timeout_sec": 1})
    assert result["reachable"] is False and "ConnectTimeout" in result["error"]


@pytest.mark.asyncio()
async def test_a_proxy_that_wants_a_login_is_no_way_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda request: httpx.Response(407))
    result = await hf_download.check_internet({})
    assert result["reachable"] is False


@pytest.mark.asyncio()
async def test_a_mirror_in_hf_endpoint_is_what_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.example.org/")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    _patch_client(monkeypatch, handler)
    await hf_download.check_internet({})
    assert seen[0].startswith("https://hf-mirror.example.org/api/models")


# -- the server's token -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [(200, {"token": "hf_abc"}, "hf_abc"), (200, {"token": None}, None), (404, {"detail": "Not Found"}, None)],
    ids=["a token", "no token on the server", "an older server"],
)
@pytest.mark.asyncio()
async def test_the_token_comes_from_the_server(status: int, body: dict[str, Any], expected: str | None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer cred"
        return httpx.Response(status, content=json.dumps(body))

    async with httpx.AsyncClient(base_url="https://server", transport=httpx.MockTransport(handler)) as client:
        assert await hf_download.fetch_token(client, "cred") == expected


@pytest.mark.asyncio()
async def test_a_server_that_says_no_stops_the_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "This machine's cluster does not download models itself."})

    async with httpx.AsyncClient(base_url="https://server", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermissionError, match="does not download models itself"):
            await hf_download.fetch_token(client, "cred")


def test_the_same_files_as_the_server_chooses() -> None:
    names = ["model.safetensors", "pytorch_model.bin", "config.json", "onnx/model.onnx", "checkpoint-500/x.safetensors"]
    assert hf_download.files_to_fetch(names) == ["model.safetensors", "config.json"]


# -- a store the agent cannot write to -------------------------------------------


def test_a_store_the_agent_cannot_write_is_named_with_the_fix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """gpu-01: a container run as root had left ~/.cache/huggingface/.locks owned by root."""
    locks = tmp_path / ".locks"
    locks.mkdir()
    real_access = hf_download.os.access
    monkeypatch.setattr(hf_download.os, "access", lambda p, mode: False if Path(p) == locks else real_access(p, mode))
    monkeypatch.setenv("USER", "sachi")

    problem = hf_download.store_problem(str(tmp_path), REPO)

    assert problem is not None and str(locks) in problem
    assert "sudo chown -R sachi:" in problem
    assert hf_download.store_problem(str(tmp_path / "elsewhere")) is None


def test_the_download_stops_before_it_starts_on_such_a_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fetched: list[dict[str, Any]] = []
    _fake_hub(monkeypatch, {"model.safetensors": 10}, fetched)
    monkeypatch.setattr(hf_download, "store_problem", lambda cache_dir, repo=None: f"{cache_dir}/.locks belongs to root")
    with pytest.raises(PermissionError, match="belongs to root"):
        hf_download.download_model(hf_repo_id=REPO, revision=None, cache_dir=str(tmp_path), token=None)
    assert fetched == []


@pytest.mark.asyncio()
async def test_the_check_says_when_the_store_is_not_writable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_client(monkeypatch, lambda request: httpx.Response(200, json=[]))
    monkeypatch.setattr(hf_download, "store_problem", lambda cache_dir, repo=None: "/store/.locks belongs to root")
    result = await hf_download.check_internet({}, model_store_root=str(tmp_path))
    assert result["reachable"] is True
    assert result["store_problem"] == "/store/.locks belongs to root"
