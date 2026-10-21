"""What an administrator sets about marking a provider's chat answers, and how it reaches the gateway.

With the provenance module on, the gateway refuses a chat to a route whose answers would carry no mark
(gateway text_marking.py): the provider's ``text_marking`` says that it marks its text itself (evidence) or
that it may answer unmarked (a reason); who and when come from the request.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.llm import LLMProvider, ProviderTarget, ProviderType
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.llm.gateway_sync import GatewaySyncService
from faden_backend.web.api.llm.dependencies import get_llm_service
from tests.test_gateway_sync_phase5 import FakeAsyncSession


async def _admin(dbsession: AsyncSession, fastapi_app: FastAPI) -> User:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    admin = User(email=f"admin-{uuid.uuid4().hex}@test.local", hashed_password="x",
                 is_verified=True, is_active=True, is_superuser=False)
    dbsession.add(admin)
    await dbsession.flush()
    await rbac.assign_role(admin.id, (await rbac.get_role_by_name("admin")).id)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    return admin


@pytest.mark.anyio
async def test_an_administrator_records_evidence_or_allows_unmarked_answers(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    admin = await _admin(dbsession, fastapi_app)
    cloud = LLMProvider(name=f"claude-{uuid.uuid4().hex[:6]}", type=ProviderType.CLOUD,
                        target=ProviderTarget.REMOTE_ENDPOINT, litellm_provider="anthropic")
    dbsession.add(cloud)
    await dbsession.commit()
    published: list[dict[str, Any] | None] = []

    class _Llm:
        async def publish_text_marking(self, runtime_dao: Any, provider: LLMProvider) -> None:
            published.append(provider.text_marking)

    fastapi_app.dependency_overrides[get_llm_service] = _Llm
    url = f"/api/llm/providers/{cloud.id}/text-marking"

    said = await client.put(url, json={"upstream": {
        "vendor": "anthropic", "evidence_url": "https://www.anthropic.com/marks", "evidence_date": "2026-10-21",
    }})
    assert said.status_code == 200, said.text
    upstream = said.json()["text_marking"]["upstream"]
    assert upstream["evidence_url"] == "https://www.anthropic.com/marks" and upstream["by"] == admin.email
    assert "allow_unmarked" not in said.json()["text_marking"]

    allowed = await client.put(url, json={"allow_unmarked": {"reason": "An internal test model, never shown."}})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["text_marking"] == {"allow_unmarked": {
        **allowed.json()["text_marking"]["allow_unmarked"], "reason": "An internal test model, never shown.",
    }}, "a part left out is cleared"

    cleared = await client.put(url, json={})
    assert cleared.json()["text_marking"] is None
    assert [bool(p) for p in published] == [True, True, False], "every change reaches the routes"

    for bad in ({"allow_unmarked": {"reason": "short"}},
                {"upstream": {"evidence_url": "http://insecure.example.com/x", "evidence_date": "2026-10-21"}}):
        assert (await client.put(url, json=bad)).status_code == 422


@pytest.mark.anyio
async def test_the_routing_row_carries_what_the_administrator_set() -> None:
    session = FakeAsyncSession(select_responses=[None])
    service = GatewaySyncService(lambda: session)
    marking = {"allow_unmarked": {"reason": "An internal test model.", "by": "a@b.c", "at": "2026-10-21"}}
    await service.publish_inference_endpoint(
        deployment_id=uuid.uuid4(), base_url="http://192.0.2.71:8000/faden-x/v1", alias="qwen",
        served_model_name="Qwen/Qwen3.8-27B-FP8", text_marking=marking,
    )
    insert = next(params for sql, params in session.executed if "INSERT INTO llm_provider_instance" in sql)
    assert json.loads(insert["node_metadata"]) == {"task": "chat", "text_marking": marking}


@pytest.mark.anyio
async def test_a_change_is_put_on_the_providers_routes_at_once() -> None:
    session = FakeAsyncSession()
    service = GatewaySyncService(lambda: session)
    runtime = uuid.uuid4()
    marking = {"allow_unmarked": {"reason": "An internal test model.", "by": "a@b.c", "at": "2026-10-21"}}
    await service.set_text_marking(runtime_ids=[runtime], source=("inference_deployment", "d1"), text_marking=marking)
    sql, params = next((s, p) for s, p in session.executed if "UPDATE llm_provider_instance" in s)
    assert "text_marking" in sql
    assert params == {"marking": json.dumps(marking), "ids": [runtime], "source_kind": "inference_deployment",
                      "source_id": "d1"}
    await service.set_text_marking(runtime_ids=[], source=None, text_marking=marking)
    assert sum("UPDATE llm_provider_instance" in s for s, _ in session.executed) == 1, "nothing to update"
