"""A machine's credential exports only the images a machine can be told to run.

``/api/node-files/images/save`` handed any image this server's Docker holds to
anyone with a node credential -- the backend's own, a database's.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.services.inference import image_cache
from faden_backend.services.inference.bundles import default_bundle_registry
from faden_backend.web.api.node_files import views


@pytest.fixture
def served(fastapi_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A server whose Docker holds every image, and a node that is signed in."""
    docker = MagicMock()
    docker.client.images.inspect = AsyncMock(return_value={"Id": "sha256:" + "a" * 64, "Size": 1})
    fastapi_app.state.docker = docker
    tar = tmp_path / "image.tar"
    tar.write_bytes(b"tar")
    monkeypatch.setattr(image_cache, "ensure_export", AsyncMock(return_value=tar))
    fastapi_app.dependency_overrides[views._authenticate_node] = lambda: SimpleNamespace(id="n1")
    yield docker
    fastapi_app.dependency_overrides.pop(views._authenticate_node, None)


async def _get(client: AsyncClient, ref: str) -> int:
    name, _, tag = ref.rpartition(":")
    r = await client.get("/api/node-files/images/save", params={"image": name, "tag": tag})
    return r.status_code


@pytest.mark.anyio()
async def test_a_catalogue_image_is_served(client: AsyncClient, served: MagicMock) -> None:
    bundle = default_bundle_registry.list_bundles()[0]
    assert await _get(client, bundle.container.image) == 200


@pytest.mark.anyio()
async def test_the_model_tls_proxy_image_is_served(client: AsyncClient, served: MagicMock) -> None:
    assert await _get(client, "faden/model-tls-nginx:1.31.6-alpine-x86_64") == 200


@pytest.mark.anyio()
@pytest.mark.parametrize("ref", ["postgres:16", "faden/backend:latest", "faden/ray-runtime:not-a-catalogue-tag"])
async def test_any_other_image_is_refused(client: AsyncClient, served: MagicMock, ref: str) -> None:
    assert await _get(client, ref) == 403
    served.client.images.inspect.assert_not_called()


@pytest.mark.anyio()
async def test_the_image_of_a_runtime_an_operator_set_up_is_served(
    client: AsyncClient, served: MagicMock, dbsession: AsyncSession,
) -> None:
    ref = "registry.example/team/vllm-custom:7"
    assert await _get(client, ref) == 403

    # A runtime row needs a provider and a model; its provider_config is all that is read.
    dbsession.execute = AsyncMock(return_value=MagicMock(scalars=lambda: iter([{"image": ref}])))
    assert ref in await views._images_a_node_may_fetch(dbsession)
