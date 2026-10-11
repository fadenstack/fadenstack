"""Tests for model pull + HF cache reconstruction."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
import pytest

from faden_agent.model_puller import ModelPullerError, pull_model


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.asyncio()
async def test_pull_model_rejects_unsafe_model_dir(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://backend.local",
    ) as client:
        with pytest.raises(ModelPullerError, match="Unsafe model_dir_name"):
            await pull_model(
                client=client,
                credential="token",
                model_sync={
                    "model_id": "m1",
                    "model_dir_name": "../escape",
                    "blobs": [],
                    "refs": [],
                    "snapshots": [],
                    "total_size": 0,
                },
                model_store_root=str(tmp_path / "models"),
            )


@pytest.mark.asyncio()
async def test_pull_model_redownloads_cached_blob_when_hash_mismatches(
    tmp_path: Path,
) -> None:
    good_data = b"good-bytes"
    bad_data = b"bad-bytes!"
    blob_hash = _sha256(good_data)
    model_dir_name = "models--org--repo"
    model_id = "mid-1"
    calls = 0

    models_root = tmp_path / "models"
    blobs_dir = models_root / model_dir_name / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    (blobs_dir / blob_hash).write_bytes(bad_data)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith(f"/blob/{blob_hash}"):
            calls += 1
            return httpx.Response(200, content=good_data)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://backend.local",
    ) as client:
        await pull_model(
            client=client,
            credential="token",
            model_sync={
                "model_id": model_id,
                "model_dir_name": model_dir_name,
                "blobs": [{"hash": blob_hash, "size": len(good_data)}],
                "refs": [],
                "snapshots": [],
                "total_size": len(good_data),
            },
            model_store_root=str(models_root),
        )

    assert calls == 1
    assert (blobs_dir / blob_hash).read_bytes() == good_data


@pytest.mark.skipif(os.name == "nt", reason="Symlink layout assertion is POSIX-focused")
@pytest.mark.asyncio()
async def test_pull_model_writes_nested_refs_and_snapshot_symlink(
    tmp_path: Path,
) -> None:
    blob_data = b'{"key":"value"}'
    blob_hash = _sha256(blob_data)
    model_dir_name = "models--org--repo"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/blob/{blob_hash}"):
            return httpx.Response(200, content=blob_data)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://backend.local",
    ) as client:
        await pull_model(
            client=client,
            credential="token",
            model_sync={
                "model_id": "mid-2",
                "model_dir_name": model_dir_name,
                "blobs": [{"hash": blob_hash, "size": len(blob_data)}],
                "refs": [{"name": "heads/main", "commit": "abc123"}],
                "snapshots": [
                    {
                        "commit": "abc123",
                        "links": [{"path": "config.json", "blob_hash": blob_hash}],
                    },
                ],
                "total_size": len(blob_data),
            },
            model_store_root=str(tmp_path / "models"),
        )

    model_dir = tmp_path / "models" / model_dir_name
    ref_file = model_dir / "refs" / "heads" / "main"
    link_path = model_dir / "snapshots" / "abc123" / "config.json"

    assert ref_file.read_text(encoding="utf-8").strip() == "abc123"
    assert link_path.is_symlink()
    assert os.path.normpath(os.readlink(link_path)) == os.path.normpath(
        os.path.join("..", "..", "blobs", blob_hash)
    )


@pytest.mark.asyncio()
async def test_pull_model_validates_content_range_on_resume(tmp_path: Path) -> None:
    blob_data = b"abcdef"
    blob_hash = _sha256(blob_data)
    model_dir_name = "models--org--repo"

    models_root = tmp_path / "models"
    blobs_dir = models_root / model_dir_name / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    (blobs_dir / f"{blob_hash}.part").write_bytes(blob_data[:2])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/blob/{blob_hash}"):
            assert request.headers.get("Range") == "bytes=2-"
            # Wrong start offset in Content-Range: should begin at 2.
            return httpx.Response(
                206,
                headers={"Content-Range": f"bytes 1-{len(blob_data) - 1}/{len(blob_data)}"},
                content=blob_data[2:],
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://backend.local",
    ) as client:
        with pytest.raises(
            ModelPullerError,
            match="Content-Range|resume offset",
        ):
            await pull_model(
                client=client,
                credential="token",
                model_sync={
                    "model_id": "mid-3",
                    "model_dir_name": model_dir_name,
                    "blobs": [{"hash": blob_hash, "size": len(blob_data)}],
                    "refs": [],
                    "snapshots": [],
                    "total_size": len(blob_data),
                },
                model_store_root=str(models_root),
            )


# -- a Hugging Face cache shared with other tools ------------------------------
#
# Found on the DGX pair: the model's blobs were links into the cache's own
# content store, ``<cache>/blobs/<xx>/<sha256>``, written by another Hugging Face
# client. The sync refused them ("Unsafe blob path"), so a machine that already
# held the model could not be given it.


def _linked_cache(tmp_path: Path, blob_hash: str, content: bytes | None) -> tuple[Path, Path, Path]:
    """A model folder whose blob is a link into a store outside it; returns (root, blob entry, store file)."""
    models_root = tmp_path / "models"
    blobs_dir = models_root / "models--org--repo" / "blobs"
    blobs_dir.mkdir(parents=True)
    store_file = models_root / "blobs" / blob_hash[:2] / blob_hash
    store_file.parent.mkdir(parents=True)
    if content is not None:
        store_file.write_bytes(content)
    try:
        (blobs_dir / blob_hash).symlink_to(store_file)
    except (OSError, NotImplementedError):  # pragma: no cover - needs privilege on Windows
        pytest.skip("symlinks not permitted in this environment")
    return models_root, blobs_dir / blob_hash, store_file


async def _pull(models_root: Path, blob_hash: str, data: bytes) -> int:
    """Sync one blob and a snapshot linking to it; returns how many blob downloads the server served."""
    served = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal served
        assert request.url.path.endswith(f"/blob/{blob_hash}")
        served += 1
        return httpx.Response(200, content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend.local") as client:
        await pull_model(
            client=client,
            credential="token",
            model_sync={
                "model_id": "m1",
                "model_dir_name": "models--org--repo",
                "blobs": [{"hash": blob_hash, "size": len(data)}],
                "refs": [{"name": "main", "commit": "abc123"}],
                "snapshots": [{"commit": "abc123", "links": [{"path": "model.safetensors", "blob_hash": blob_hash}]}],
                "total_size": len(data),
            },
            model_store_root=str(models_root),
        )
    return served


@pytest.mark.asyncio()
async def test_a_linked_blob_that_holds_the_content_is_used_in_place(tmp_path: Path) -> None:
    data = b"weights"
    blob_hash = _sha256(data)
    models_root, entry, store_file = _linked_cache(tmp_path, blob_hash, data)

    assert await _pull(models_root, blob_hash, data) == 0  # nothing downloaded

    assert entry.is_symlink() and entry.resolve() == store_file.resolve()
    snapshot = models_root / "models--org--repo" / "snapshots" / "abc123" / "model.safetensors"
    assert snapshot.read_bytes() == data


@pytest.mark.asyncio()
async def test_a_linked_blob_with_other_content_is_replaced_not_written_through(tmp_path: Path) -> None:
    data = b"weights"
    blob_hash = _sha256(data)
    models_root, entry, store_file = _linked_cache(tmp_path, blob_hash, b"something else")

    assert await _pull(models_root, blob_hash, data) == 1

    assert not entry.is_symlink() and entry.read_bytes() == data
    assert store_file.read_bytes() == b"something else"  # the other tool's store is untouched


@pytest.mark.asyncio()
async def test_a_dangling_link_is_replaced_by_the_file(tmp_path: Path) -> None:
    data = b"weights"
    blob_hash = _sha256(data)
    models_root, entry, store_file = _linked_cache(tmp_path, blob_hash, None)

    assert await _pull(models_root, blob_hash, data) == 1

    assert not entry.is_symlink() and entry.read_bytes() == data
    assert not store_file.exists()
