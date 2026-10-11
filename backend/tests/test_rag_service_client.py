"""The full RAG module's proxy: an id from the URL reaches the RAG service as one path segment.

The calls carry the service's own token, so an id like ``..`` or ``x?y`` must not lead to another
of its endpoints.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from starlette import status

from faden_backend.services.rag.client import RagServiceClient


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []

    async def request(self: httpx.AsyncClient, method: str, url: str, **_: Any) -> httpx.Response:
        urls.append((method, url))
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    return urls


def _calls(rag: RagServiceClient, value: str) -> list[Any]:
    return [
        lambda: rag.run_collector(value),
        lambda: rag.get_job(value),
        lambda: rag.update_container(value, {}),
        lambda: rag.delete_container(value),
        lambda: rag.get_draft(value),
        lambda: rag.patch_draft(value, {}),
        lambda: rag.publish_draft(value, {}),
        lambda: rag.get_publish(value),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("bad", ["..", ".", "x?y=1", "x#y", "a/b", "%2e%2e", "x\n", "", "a" * 129])
async def test_an_id_that_is_not_one_segment_is_refused(bad: str, sent: list[tuple[str, str]]) -> None:
    rag = RagServiceClient(base_url="http://rag", service_token="t")  # noqa: S106
    for call in _calls(rag, bad):
        with pytest.raises(HTTPException) as caught:
            await call()
        assert caught.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert sent == []


@pytest.mark.anyio
async def test_ids_reach_their_own_endpoint(sent: list[tuple[str, str]]) -> None:
    rag = RagServiceClient(base_url="http://rag", service_token="t")  # noqa: S106
    ident = "0b8f3c2e-5d4a-4f1b-9e7c-2a1d3c4b5e6f"
    for call in _calls(rag, ident):
        await call()
    assert all(ident in url for _, url in sent)
    assert sent[0] == ("POST", f"http://rag/internal/admin/collectors/{ident}/run")


@pytest.mark.anyio
async def test_the_tree_query_keeps_to_its_two_parameters(sent: list[tuple[str, str]]) -> None:
    rag = RagServiceClient(base_url="http://rag", service_token="t")  # noqa: S106
    await rag.list_containers_tree(tenant_id="a&admin=1", workspace_id="w#x")
    assert sent == [("GET", "http://rag/internal/admin/containers/tree?tenant_id=a%26admin%3D1&workspace_id=w%23x")]
