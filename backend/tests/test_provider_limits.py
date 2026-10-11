"""Remote providers have no limit: an admin can add as many as they need.

A limit is only there when a plugin sets one; reaching it says so plainly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.llm_dao import ProviderDAO
from faden_backend.db.models.llm import ProviderTarget, ProviderType
from faden_backend.services.llm.service import LLMService

pytestmark = pytest.mark.anyio


async def _remote(service: LLMService, dao: ProviderDAO, n: int) -> None:
    await service.create_provider(
        dao,
        name=f"remote {n}",
        type_=ProviderType.VLLM,
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url=f"http://192.0.2.{n + 1}:8000/v1",
    )


async def test_a_fourth_and_fifth_remote_provider_are_added(dbsession: AsyncSession) -> None:
    service = LLMService(MagicMock(), _caps={})  # what the backend starts with
    dao = ProviderDAO(dbsession)
    for n in range(5):
        await _remote(service, dao, n)
    assert await dao.count_by_target(ProviderTarget.REMOTE_ENDPOINT) == 5


async def test_a_limit_a_plugin_sets_is_named_plainly(dbsession: AsyncSession) -> None:
    service = LLMService(MagicMock(), _caps={"remote_providers": 1})
    dao = ProviderDAO(dbsession)
    await _remote(service, dao, 0)
    with pytest.raises(HTTPException) as refused:
        await _remote(service, dao, 1)
    assert refused.value.status_code == 402
    assert refused.value.detail == "Provider limit reached: this server allows 1 remote providers."
