"""A picture in a chat with the PII check on goes through, and the person is told (0.7.2 G).

The PII check refused every message with a picture (``pii_unsupported_surface``), and the gateway answered "PII
service unavailable and fail_action=block". The owner: tell the person rather than refuse, so the limit shows. The
check reads text only: a picture goes on as it came, the chat's events say so (``faden.pii_pictures_unchecked``),
and the request log counts it. A real failure of the check is still refused.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import LLMGatewayRequestLog
from faden_gateway.services.gateway import faden_events
from faden_gateway.services.gateway.pii_client import PIIClient, SanitizeResult
from faden_gateway.services.gateway.pii_policy import parse_pii_policy
from faden_gateway.services.registry import service_registry
from tests.test_pii_module_off import ALIAS, _model_answers, _Observability, _policy, _seed, _token

#: A 1x1 PNG: the gateway takes pictures by their first bytes (picture_check.py).
PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PICTURE = {"type": "image_url", "image_url": {"url": PNG}}


def _payload(*pictures: dict[str, Any]) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": [{"type": "text", "text": "I am Alice Meyer"}, *pictures]}]}


def _echoing(change: Any = None) -> httpx.MockTransport:
    """A PII service that masks the name and passes the pictures on (or, with *change*, alters them)."""

    def answer(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)["payload"]
        message = payload["messages"][0]
        message["content"][0]["text"] = "I am <PERSON>"
        if change is not None:
            change(message["content"])
        return httpx.Response(200, json={
            "sanitized_payload": payload, "entities_found": 1, "contract_version": 2,
            "coverage_status": "complete", "profile_fingerprint": "test-v1",
            "pii_report": [{"entity_type": "PERSON", "start": 5, "end": 16, "score": 0.9, "text": "Alice Meyer"}],
            "pictures_unchecked": sum(1 for part in message["content"] if part.get("type") == "image_url"),
        })

    return httpx.MockTransport(answer)


async def _sanitize(transport: httpx.MockTransport, payload: dict[str, Any]) -> SanitizeResult:
    policy = parse_pii_policy(_policy())
    assert policy is not None
    async with httpx.AsyncClient(transport=transport) as http:
        return await PIIClient(base_url="http://pii.local", http_client=http).sanitize(payload=payload, policy=policy)


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, 1, 3])
async def test_the_client_counts_the_pictures_the_check_passed_on(count: int) -> None:
    result = await _sanitize(_echoing(), _payload(*[PICTURE] * count))
    assert result.pictures_unchecked == count
    assert result.sanitized_payload["messages"][0]["content"][1:] == [PICTURE] * count
    assert result.entity_types == {"PERSON": 1}


@pytest.mark.anyio
@pytest.mark.parametrize("change", [
    lambda parts: parts[1]["image_url"].update(url="data:image/png;base64,AAAA"),
    lambda parts: parts[1].update(type="text", text="x"),
], ids=["url-changed", "turned-into-text"])
async def test_a_response_that_altered_a_picture_is_refused(change: Any) -> None:
    with pytest.raises(ValueError, match=r"picture changed|Invalid PII response"):
        await _sanitize(_echoing(change), _payload(PICTURE))


def test_the_event_counts_and_words_it() -> None:
    assert faden_events.pictures_unchecked(0) is None
    one = faden_events.pictures_unchecked(1)
    assert one == {"type": "CUSTOM", "name": "faden.pii_pictures_unchecked", "value": {
        "count": 1, "message": "Pictures are not checked for personal data, so the picture went to the model as it is.",
    }}
    three = faden_events.pictures_unchecked(3)
    assert three is not None and three["value"]["count"] == 3
    assert three["value"]["message"].endswith("so 3 pictures went to the model as they are.")


@pytest.mark.anyio
async def test_a_chat_with_a_picture_goes_through_under_block_and_says_so(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("block"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def masks(self: PIIClient, *, payload: dict[str, Any], **kwargs: object) -> SanitizeResult:  # noqa: ARG001
        clean = json.loads(json.dumps(payload))
        clean["messages"][0]["content"][0]["text"] = "I am <PERSON>"
        return SanitizeResult(sanitized_payload=clean, entities_found=1, pii_detected=True,
                              entity_types={"PERSON": 1}, pictures_unchecked=1)

    monkeypatch.setattr(PIIClient, "sanitize", masks)
    try:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {_token()}", faden_events.HEADER: "ag-ui"},
            json={"model": ALIAS, **_payload(PICTURE)},
        )
    finally:
        service_registry.configure("pii", enabled=False, url=None)

    assert r.status_code == 200, r.text
    parts = sent[0]["messages"][-1]["content"]
    assert {"type": "text", "text": "I am <PERSON>"} in parts and PICTURE in parts
    names = [event.get("name") for event in r.json().get("faden", {}).get("events", [])]
    assert "faden.pii_pictures_unchecked" in names
    row = (await db_session.execute(select(LLMGatewayRequestLog))).scalars().all()[-1]
    assert row.pii_pictures_unchecked == 1


@pytest.mark.anyio
async def test_without_a_picture_no_event_and_no_count(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("block"))
    fastapi_app.state.gateway_observability = _Observability()
    _model_answers(monkeypatch, [])
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def masks(self: PIIClient, *, payload: dict[str, Any], **kwargs: object) -> SanitizeResult:  # noqa: ARG001
        return SanitizeResult(sanitized_payload=payload)

    monkeypatch.setattr(PIIClient, "sanitize", masks)
    try:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {_token()}", faden_events.HEADER: "ag-ui"},
            json={"model": ALIAS, "messages": [{"role": "user", "content": "Hello"}]},
        )
    finally:
        service_registry.configure("pii", enabled=False, url=None)

    assert r.status_code == 200, r.text
    assert "faden.pii_pictures_unchecked" not in [
        e.get("name") for e in r.json().get("faden", {}).get("events", [])
    ]
    row = (await db_session.execute(select(LLMGatewayRequestLog))).scalars().all()[-1]
    assert row.pii_pictures_unchecked is None


# ── The organisation's choice: refuse pictures while the check runs ───────────────────────────────────


def _refusing(fail_action: str = "block", **egress: Any) -> dict[str, Any]:
    policy = _policy(fail_action)
    policy["egress"] = {**policy["egress"], "refuse_pictures": True, **egress}
    return policy


@pytest.mark.parametrize(("raw", "on"), [(True, True), (False, False), ("true", False), (1, False), (None, False)])
def test_only_a_true_switches_the_refusal_on(raw: Any, on: bool) -> None:
    policy = _policy()
    policy["egress"]["refuse_pictures"] = raw
    parsed = parse_pii_policy(policy)
    assert parsed is not None and parsed.egress.refuse_pictures is on


@pytest.mark.parametrize("weaken", [False, True])
def test_a_chats_own_settings_cannot_switch_the_refusal_off(weaken: bool) -> None:
    from faden_gateway.services.gateway.pii_policy import SessionPIIOverride, clamp_and_merge  # noqa: PLC0415

    floor = parse_pii_policy(_refusing())
    assert floor is not None
    merged = clamp_and_merge(
        floor, SessionPIIOverride(egress_enabled_for_cloud=False, egress_fail_action="allow"), allow_weaken=weaken,
    )
    assert merged.egress.refuse_pictures is True


async def _chat_with_picture(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    *, policy: dict[str, Any], sent: list[dict[str, Any]], scans: list[int],
) -> httpx.Response:
    await _seed(db_session, pii=policy)
    fastapi_app.state.gateway_observability = _Observability()
    _model_answers(monkeypatch, sent)
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def masks(self: PIIClient, *, payload: dict[str, Any], **kwargs: object) -> SanitizeResult:  # noqa: ARG001
        scans.append(1)
        return SanitizeResult(sanitized_payload=payload, pictures_unchecked=1)

    monkeypatch.setattr(PIIClient, "sanitize", masks)
    try:
        return await client.post(
            "/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"},
            json={"model": ALIAS, **_payload(PICTURE)},
        )
    finally:
        service_registry.configure("pii", enabled=False, url=None)


@pytest.mark.anyio
@pytest.mark.parametrize("fail_action", ["block", "allow", "fallback_to_local"])
async def test_with_the_refusal_on_a_picture_is_refused_whatever_fail_action_says(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    fail_action: str,
) -> None:
    sent: list[dict[str, Any]] = []
    scans: list[int] = []
    r = await _chat_with_picture(
        fastapi_app, client, db_session, monkeypatch, policy=_refusing(fail_action), sent=sent, scans=scans,
    )

    assert r.status_code == 403, r.text
    error = r.json()["error"]
    assert error["code"] == "pii_pictures_refused" and error["param"] is None
    assert error["message"].startswith("Pictures are not checked for personal data, and your organisation")
    assert sent == [] and scans == [], "nothing was checked or sent"
    row = (await db_session.execute(select(LLMGatewayRequestLog))).scalars().all()[-1]
    assert row.error_code == "pii_pictures_refused" and row.status_code == 403


@pytest.mark.anyio
async def test_with_the_refusal_on_a_request_the_check_does_not_cover_goes_on(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal holds where the check runs: here only for local models, and the provider is a cloud one."""
    sent: list[dict[str, Any]] = []
    policy = _refusing(enabled_for_cloud=False, enabled_for_local=True)
    r = await _chat_with_picture(
        fastapi_app, client, db_session, monkeypatch, policy=policy, sent=sent, scans=[],
    )
    assert r.status_code == 200, r.text
    assert PICTURE in sent[0]["messages"][-1]["content"]


def test_a_picture_from_the_projects_files_is_named() -> None:
    from faden_gateway.services.gateway.errors import GatewayError  # noqa: PLC0415
    from faden_gateway.services.gateway.service import _refuse_pictures  # noqa: PLC0415

    _refuse_pictures({"messages": [{"role": "user", "content": "no picture"}]}, ["plan.png"])  # nothing to refuse
    with pytest.raises(GatewayError) as refused:
        _refuse_pictures(_payload(PICTURE), ["plan.png"])
    assert refused.value.status_code == 403 and refused.value.param == "project_files"
    assert "Remove plan.png from the project's Files" in refused.value.message
    assert refused.value.extra == {"faden": {"pictures_refused": {"project_files": ["plan.png"]}}}


@pytest.mark.anyio
@pytest.mark.parametrize("tools", [True, False], ids=["route-calls-tools", "route-without-tools"])
async def test_a_refused_picture_from_the_projects_files_is_named_in_the_chat(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    tools: bool,
) -> None:
    """The project's picture goes with every chat of the project: the refusal names it, on any route."""
    from faden_gateway.db.models.gateway import (  # noqa: PLC0415
        AttachmentScope,
        ChatAttachment,
        ChatProject,
        ChatSession,
        ExtractionStatus,
        ProviderType,
    )
    from tests.test_gateway_chat_pictures import _Store  # noqa: PLC0415
    from tests.test_gateway_pipeline_combined import ALIAS as PIPELINE_ALIAS  # noqa: PLC0415
    from tests.test_gateway_pipeline_combined import _model_answers as answering  # noqa: PLC0415
    from tests.test_gateway_pipeline_combined import _seed as seed  # noqa: PLC0415

    await seed(db_session, provider=ProviderType.VLLM, pii=_refusing(enabled_for_local=True),
               node_metadata={"task": "chat", "vision": True, "tools": tools})
    fastapi_app.state.gateway_observability = _Observability()
    fastapi_app.state.chat_file_store = _Store()
    project = ChatProject(tenant_id="tenant-a", user_id="user-1", name="Plans")
    db_session.add(project)
    await db_session.flush()
    chat = ChatSession(tenant_id="tenant-a", user_id="user-1", title="t", project_id=project.id)
    db_session.add(chat)
    db_session.add(ChatAttachment(
        tenant_id="tenant-a", user_id="user-1", project_id=project.id, filename="plan.png", content_type="image/png",
        size_bytes=10, storage_key="k/plan.png", extraction_status=ExtractionStatus.SKIPPED,
        scope=AttachmentScope.PROJECT,
    ))
    await db_session.commit()
    sent: list[dict[str, Any]] = []
    answering(monkeypatch, "ok", sent)
    service_registry.configure("pii", enabled=True, url="http://pii.local")
    try:
        r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
            "model": PIPELINE_ALIAS, "session_id": str(chat.id),
            "messages": [{"role": "user", "content": "Hello"}],
        })
    finally:
        service_registry.configure("pii", enabled=False, url=None)

    assert r.status_code == 403, r.text
    assert r.json()["error"]["param"] == "project_files"
    assert "Remove plan.png from the project's Files" in r.json()["error"]["message"]
    assert r.json()["faden"] == {"pictures_refused": {"project_files": ["plan.png"]}}
    assert sent == []
