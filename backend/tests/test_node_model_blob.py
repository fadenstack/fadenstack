"""A machine downloads a model's blob from the server's cache: that blob, and nothing beside it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.web.api.node_files import views

pytestmark = pytest.mark.anyio

BLOB = "ab12" * 16


@pytest.fixture
def cache(fastapi_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A model whose cache holds one blob, and a node that is signed in."""
    model_dir = tmp_path / "models--org--m"
    (model_dir / "blobs").mkdir(parents=True)
    (model_dir / "blobs" / BLOB).write_bytes(b"0123456789")
    (tmp_path / "beside").write_bytes(b"not a blob")

    class _Models:
        def __init__(self, _session: object) -> None:
            pass

        async def get(self, _model_id: object) -> SimpleNamespace:
            return SimpleNamespace(hf_repo_id="org/m")

    @asynccontextmanager
    async def session() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(views, "ModelDAO", _Models)
    monkeypatch.setattr(views, "_model_cache_dir", lambda _repo: model_dir)
    monkeypatch.setattr(fastapi_app.state, "db_session_factory", session, raising=False)
    fastapi_app.dependency_overrides[views._authenticate_node] = lambda: SimpleNamespace(id="n1")
    yield model_dir
    fastapi_app.dependency_overrides.pop(views._authenticate_node, None)


def _url(blob: str) -> str:
    return f"/api/node-files/models/{uuid.uuid4()}/blob/{blob}"


async def test_a_blob_is_served_whole_or_in_part(client: AsyncClient, cache: Path) -> None:
    resp = await client.get(_url(BLOB))
    assert resp.status_code == 200
    assert resp.content == b"0123456789"

    resp = await client.get(_url(BLOB), headers={"Range": "bytes=2-5"})
    assert resp.status_code == 206
    assert resp.content == b"2345"
    assert resp.headers["content-range"] == "bytes 2-5/10"


async def test_a_name_that_is_not_a_blob_is_refused(client: AsyncClient, cache: Path) -> None:
    assert (await client.get(_url("ab12" * 15 + "ffff"))).status_code == 404
    assert (await client.get(_url("..%2Fbeside"))).status_code in (400, 404)
    assert (await client.get(_url("abc.txt"))).status_code == 400


async def test_a_link_out_of_the_blobs_folder_is_not_followed(client: AsyncClient, cache: Path) -> None:
    try:
        (cache / "blobs" / "abcd").symlink_to(cache.parent / "beside")
    except OSError:
        pytest.skip("this system makes no symbolic links for this user")
    resp = await client.get(_url("abcd"))
    assert resp.status_code == 400
    assert b"not a blob" not in resp.content
