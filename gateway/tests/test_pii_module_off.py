"""A PII policy saved for the tenant is not skipped when the PII module is off.

Without a PII client the payload used to go out unscanned, without a word. A saved
policy now fails the way a scan that errors does: ``fail_action`` decides. The system
default policy -- which the backend seeds on every install, cloud routes scanned and
``fail_action`` block -- is not enforced without the module, or an install that never
switched PII on would refuse every cloud chat.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import (
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    PrivacyMode,
    ProviderHealthStatus,
    ProviderType,
    TenantLLMPolicy,
)
from faden_gateway.services.gateway import service as gateway_service
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from faden_gateway.services.gateway.observability import GatewayTraceContext
from faden_gateway.services.gateway.pii_policy import parse_pii_policy, tenant_set_policy
from faden_gateway.services.registry import service_registry
from faden_gateway.settings import settings

TEST_JWT_SECRET = "test-secret-32-bytes-minimum-value"
ALIAS = "qwen3-32b"

#: What the backend seeds on every install (backend/services/pii_default_policy.py): no types named, the
#: detector's thresholds.
SEEDED_DEFAULT: dict[str, Any] = {
    "telemetry": {"enabled": False, "mode": "sanitized", "store_raw": False},
    "egress": {"enabled_for_cloud": True, "enabled_for_local": False, "mode": "redact", "fail_action": "block"},
    "presidio": {"language": "en", "threshold": 0.35, "threshold_mode": "profile", "entities": []},
}


def _policy(fail_action: str = "block", *, telemetry: bool = False) -> dict[str, Any]:
    return {
        "telemetry": {"enabled": telemetry, "mode": "sanitized"},
        "egress": {"enabled_for_cloud": True, "enabled_for_local": False, "mode": "redact", "fail_action": fail_action},
        "presidio": {"language": "en", "threshold": 0.5, "entities": ["PERSON"]},
    }


def _token() -> str:
    return jwt.encode({"sub": "user-1", "tenant_id": "tenant-a"}, TEST_JWT_SECRET, algorithm="HS256")


class _Observability:
    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def start_request_trace(self, **kwargs: object) -> GatewayTraceContext:
        self.payloads.append(kwargs.get("payload"))
        return GatewayTraceContext(
            trace_id="t", observation=None, endpoint="/v1/chat/completions",
            privacy_mode=PrivacyMode.METADATA_ONLY,
        )

    def record_success(self, *_: object, **__: object) -> None: ...
    def record_failure(self, *_: object, **__: object) -> None: ...
    def finalize_stream(self, *_: object, **__: object) -> None: ...


async def _seed(session: AsyncSession, *, pii: dict[str, Any] | None, with_tenant_row: bool = True) -> None:
    now = datetime.now(timezone.utc)
    instance_id = uuid.uuid4()
    rows: list[Any] = [
        LLMModelAlias(alias=ALIAS, description="", enabled=True, created_at=now, updated_at=now),
        LLMProviderInstance(
            id=instance_id, type=ProviderType.REMOTE_OPENAI, base_url="http://upstream.local", enabled=True,
            weight=1.0, max_concurrency=4, health_status=ProviderHealthStatus.HEALTHY,
            created_at=now, updated_at=now,
        ),
        LLMPoolMembership(model_alias=ALIAS, provider_instance_id=instance_id, enabled=True),
    ]
    if with_tenant_row:
        rows.append(TenantLLMPolicy(
            tenant_id="tenant-a", privacy_mode=PrivacyMode.METADATA_ONLY,
            allowed_model_aliases=[ALIAS], allowed_provider_types=[ProviderType.REMOTE_OPENAI.value],
            rpm_limit=100, tpm_limit=1_000_000, pii_config=pii, created_at=now, updated_at=now,
        ))
    session.add_all(rows)
    await session.commit()


def _model_answers(monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, Any]]) -> None:
    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        sent.append(kwargs["payload"])
        return CompletionResult(status_code=200, payload={
            "id": "c", "object": "chat.completion", "model": ALIAS,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(LLMAdapter, "completion", completion)


@pytest.fixture(autouse=True)
def _module_off(monkeypatch: pytest.MonkeyPatch) -> Any:
    service_registry.configure("pii", enabled=False, url=None)
    monkeypatch.setattr(gateway_service, "_default_policy_noted", False)
    yield
    service_registry.configure("pii", enabled=False, url=None)


async def _chat(client: AsyncClient) -> httpx.Response:
    return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "I am Alice Meyer"}],
    })


# ── A saved policy, the module off ────────────────────────────────


@pytest.mark.anyio
async def test_a_saved_policy_that_blocks_refuses_the_chat_and_says_what_to_do(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("block"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)

    r = await _chat(client)

    assert r.status_code == 502
    error = r.json()["error"]
    assert error["code"] == "pii_service_unavailable"
    assert "start the PII module" in error["message"] and "switch the policy off" in error["message"]
    assert sent == [], "nothing reached the provider"


@pytest.mark.anyio
async def test_a_saved_policy_with_fail_action_allow_lets_the_chat_through(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("allow"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)

    r = await _chat(client)

    assert r.status_code == 200
    assert "Alice Meyer" in json.dumps(sent[0]), "the admin chose to allow an unscanned chat"


@pytest.mark.anyio
async def test_fallback_to_local_without_a_local_route_refuses(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("fallback_to_local"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)

    r = await _chat(client)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "pii_fallback_no_local_provider"
    assert sent == []


@pytest.mark.anyio
async def test_the_trace_of_a_saved_telemetry_policy_holds_no_text_when_the_module_is_off(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("allow", telemetry=True))
    observability = _Observability()
    fastapi_app.state.gateway_observability = observability
    _model_answers(monkeypatch, [])

    assert (await _chat(client)).status_code == 200

    assert "Alice" not in json.dumps(observability.payloads[0])
    assert observability.payloads[0]["_pii_mode"] == "fallback"


# ── An install that never used PII is unaffected ──────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("pii", "default", "tenant_row"),
    [
        (None, None, True),
        (None, None, False),
        (None, SEEDED_DEFAULT, True),
        ({}, SEEDED_DEFAULT, True),
        (None, SEEDED_DEFAULT, False),
    ],
    ids=["no-policy", "no-tenant-row", "seeded-default", "empty-tenant-config", "seeded-default-no-tenant-row"],
)
async def test_an_install_that_never_used_pii_chats_as_before(
    pii: dict[str, Any] | None, default: dict[str, Any] | None, tenant_row: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend seeds ``SEEDED_DEFAULT`` everywhere: with the module off it must not block a cloud chat."""
    await _seed(db_session, pii=pii, with_tenant_row=tenant_row)
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "pii_default_policy", default, raising=False)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)

    r = await _chat(client)

    assert r.status_code == 200, r.text
    assert len(sent) == 1


# ── Tool results ──────────────────────────────────────────────────


def _tool_request(policy: dict[str, Any] | None, *, saved: bool) -> Any:
    cloud = SimpleNamespace(provider_type=SimpleNamespace(value="remote_openai"))
    return SimpleNamespace(
        decision=SimpleNamespace(candidate=cloud),
        pii_policy=parse_pii_policy(policy) if policy else None,
        pii_policy_saved=saved,
        token_mapping=None,
    )


async def _scan_tool_results(req: Any, answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    service = SimpleNamespace(pii_client=None)
    return await gateway_service.GatewayService._scan_tool_results(service, req, answers)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_tool_results_are_not_sent_on_when_a_saved_policy_needs_the_missing_module() -> None:
    answers = [{"role": "tool", "content": "Alice Meyer lives in Munich"}]
    with pytest.raises(GatewayError) as stopped:
        await _scan_tool_results(_tool_request(_policy("block"), saved=True), answers)
    assert stopped.value.code == "pii_service_unavailable"
    assert "start the PII module" in stopped.value.message


@pytest.mark.anyio
async def test_tool_results_follow_fail_action_allow_when_the_module_is_off() -> None:
    answers = [{"role": "tool", "content": "Alice Meyer lives in Munich"}]
    assert await _scan_tool_results(_tool_request(_policy("allow"), saved=True), answers) == answers


@pytest.mark.anyio
async def test_tool_results_pass_on_an_install_that_never_used_pii() -> None:
    answers = [{"role": "tool", "content": "Alice Meyer lives in Munich"}]
    assert await _scan_tool_results(_tool_request(SEEDED_DEFAULT, saved=False), answers) == answers


# ── What counts as a saved policy ─────────────────────────────────


def test_only_a_policy_saved_for_the_tenant_counts_as_saved() -> None:
    assert tenant_set_policy(SimpleNamespace(pii_config=_policy())) is True
    assert tenant_set_policy(SimpleNamespace(pii_config=None)) is False
    assert tenant_set_policy(SimpleNamespace(pii_config={})) is False
    assert tenant_set_policy(None) is False
    # Saved but everything off: parses to no policy, so the system default would apply.
    off = {"egress": {"enabled_for_cloud": False, "enabled_for_local": False}, "telemetry": {"enabled": False}}
    assert tenant_set_policy(SimpleNamespace(pii_config=off)) is False
