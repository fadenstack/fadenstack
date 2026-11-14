"""A new tool prefix is lowercase letters, digits, "_" and "-", up to 32 characters.

A tool is named ``mcp.<prefix>.<tool>``; the gateway gives the model
``mcp_<prefix>_<tool>``, which providers take up to 64 characters. A prefix
could be anything up to 128: a dot in it was read wrongly by the gateway, and a
long one left no room for the tool's name. Prefixes made before stay as they are.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from faden_mcp.web.api.admin import views
from faden_mcp.web.api.admin.schemas import RegisterServerRequest, UpdateServerRequest

TOLD = "lowercase letters, digits, '_' and '-'"


@pytest.mark.parametrize(
    "prefix", ["github", "gh", "brave_search", "my-server", "a" * 32, "x1"],
)
def test_a_prefix_of_the_rule_is_taken(prefix: str) -> None:
    """Nothing is raised."""
    views._check_tool_prefix(prefix)  # noqa: SLF001


@pytest.mark.parametrize(
    "prefix", ["", "GitHub", "a.b", "my server", "a" * 33, "ä", "a/b", "gh:1"],
)
def test_any_other_prefix_is_refused_and_told_why(prefix: str) -> None:
    """A 422 that names the rule."""
    with pytest.raises(HTTPException) as refused:
        views._check_tool_prefix(prefix)  # noqa: SLF001
    assert refused.value.status_code == 422
    assert TOLD in str(refused.value.detail)


@pytest.mark.asyncio
async def test_a_server_with_another_prefix_is_not_registered() -> None:
    """Refused before anything is stored or connected: the session is never used."""
    body = RegisterServerRequest(
        name="Docs",
        transport="streamable_http",
        url="http://docs:8000/mcp",
        tool_prefix="Docs.Search",
    )
    with pytest.raises(HTTPException) as refused:
        await views.register_server(
            body, request=None, auth=None, session=None,  # type: ignore[arg-type]
        )
    assert refused.value.status_code == 422


class _StoredError(Exception):
    """The update got past the checks, to the store."""


class _Dao:
    def __init__(self, session: Any) -> None:
        pass

    async def get_server(self, server_id: uuid.UUID) -> Any:
        return SimpleNamespace(id=server_id, tool_prefix="Legacy.Prefix")

    async def update_server(self, server_id: uuid.UUID, **fields: Any) -> Any:
        raise _StoredError(fields)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "stored"),
    [
        (None, True),
        ("Legacy.Prefix", True),
        ("legacy_prefix", True),
        ("Another.One", False),
        ("", False),
    ],
    ids=[
        "left-out", "kept-as-it-was", "changed-to-the-rule", "changed-against-it",
        "emptied",
    ],
)
async def test_a_prefix_made_before_the_rule_stays_but_a_new_one_keeps_it(
    prefix: str | None, stored: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server keeps its old prefix through any edit; a new one follows the rule."""
    monkeypatch.setattr(views, "MCPDao", _Dao)
    body = UpdateServerRequest(tool_prefix=prefix, timeout_sec=30)
    with pytest.raises(_StoredError if stored else HTTPException) as raised:
        await views.update_server(
            uuid.uuid4(), body, request=None, auth=None, session=None,  # type: ignore[arg-type]
        )
    if not stored:
        assert raised.value.status_code == 422  # type: ignore[union-attr]
