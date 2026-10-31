"""Authenticated explicit host checks share central policy, never a model/native executor."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import AgentClientEvent, ProviderType
from faden_gateway.services.gateway import governance as gov
from faden_gateway.services.gateway import tool_classes as tc
from tests.test_agent_routes import _Backend, _headers, _profile
from tests.test_gateway_pipeline_combined import _seed
from tests.test_governance import _rule

SCHEMA = "a" * 64
pytest_plugins = ("tests.test_agent_routes",)


def command(**changes: Any) -> dict[str, Any]:
    return {
        "name": "nc_actions_undo",
        "arguments": {"action_id": "fixture-action"},
        "tool_mode": "auto",
        "origin": "user_history",
        "phase": "prepare",
        "schema_sha256": SCHEMA,
        **changes,
    }


async def configure(db: AsyncSession, backend: _Backend, name: str = "nc_actions_undo") -> None:
    await _seed(db, provider=ProviderType.VLLM, pii=None)
    backend.put(
        _profile(
            host_tool_contract=(
                {
                    "name": name,
                    "risk": "destructive",
                    "schema_sha256": SCHEMA,
                },
            ),
        ),
    )
    gov.tool_rules.invalidate()
    tc.class_rules.invalidate()


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["nc_actions_undo", "nc_actions_redo", "nc_actions_reconcile", "nc_actions_revalidate"])
async def test_direct_command_is_individual_and_the_actor_is_authenticated(
    name: str, client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    await configure(db_session, backend, name)
    response = await client.post(
        "/v1/agents/eplan-assistant/tools/check", headers=_headers(), json=command(name=name),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["decision"], body["tool_class"]) == ("ask", "destructive")
    assert body["principal"]["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["principal"]["device_id"] == "device-7"
    prepared = {
        key: body[value]
        for key, value in [
            ("prepared_profile", "profile_stamp"),
            ("prepared_arguments", "arguments_stamp"),
            ("prepared_decision", "decision_stamp"),
        ]
    }
    checked = await client.post(
        "/v1/agents/eplan-assistant/tools/check",
        headers=_headers(),
        json=command(name=name, phase="dispatch", **prepared),
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["decision_stamp"] == body["decision_stamp"]
    events = (
        (
            await db_session.execute(
                select(AgentClientEvent).where(
                    AgentClientEvent.type == "host_tool.checked",
                ),
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 2
    assert all(
        "action_id" not in json.dumps(event.data)
        and "fixture-action" not in json.dumps(event.data)
        for event in events
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["off", "read_only"])
async def test_restrictive_mode_never_authorizes_history_mutation(
    mode: str, client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    await configure(db_session, backend)
    result = await client.post(
        "/v1/agents/eplan-assistant/tools/check",
        headers=_headers(),
        json=command(tool_mode=mode),
    )
    assert result.status_code == 200, result.text
    assert result.json()["decision"] == "deny"


@pytest.mark.anyio
async def test_current_central_deny_rule_is_used(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    await configure(db_session, backend)
    await _rule(db_session, "nc_actions_*", "deny", "History is disabled here.")
    gov.tool_rules.invalidate()
    result = await client.post(
        "/v1/agents/eplan-assistant/tools/check", headers=_headers(), json=command(),
    )
    assert result.status_code == 200, result.text
    assert result.json()["decision"] == "deny"


@pytest.mark.anyio
async def test_no_contract_and_unpinned_schema_fail_closed(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    await configure(db_session, backend)
    absent = await client.post(
        "/v1/agents/eplan-assistant/tools/check",
        headers=_headers(),
        json=command(name="made_up_tool"),
    )
    assert absent.status_code == 200 and absent.json()["decision"] == "deny"
    backend.put(
        _profile(
            host_tool_contract=({"name": "nc_actions_undo", "risk": "destructive"},),
        ),
    )
    # Agent directory has a documented cache boundary; explicitly invalidate in this fixture.
    backend.agents.clear()
    backend.put(
        _profile(
            slug="unpinned",
            host_tool_contract=({"name": "nc_actions_undo", "risk": "destructive"},),
        ),
    )
    unpinned = await client.post(
        "/v1/agents/unpinned/tools/check", headers=_headers(), json=command(),
    )
    assert (
        unpinned.status_code == 409
        and unpinned.json()["error"]["code"] == "host_schema_required"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        {"arguments": {"action_id": "different"}},
        {"prepared_profile": "b" * 64},
        {"schema_sha256": "b" * 64},
    ],
)
async def test_changed_arguments_profile_or_schema_never_reuses_a_review(
    change: dict[str, Any],
    client: AsyncClient,
    db_session: AsyncSession,
    backend: _Backend,
) -> None:
    await configure(db_session, backend)
    prepared = (
        await client.post(
            "/v1/agents/eplan-assistant/tools/check", headers=_headers(), json=command(),
        )
    ).json()
    fields = {
        "prepared_profile": prepared["profile_stamp"],
        "prepared_arguments": prepared["arguments_stamp"],
        "prepared_decision": prepared["decision_stamp"],
        **change,
    }
    response = await client.post(
        "/v1/agents/eplan-assistant/tools/check",
        headers=_headers(),
        json=command(phase="dispatch", **fields),
    )
    assert response.status_code == 409, response.text


@pytest.mark.anyio
async def test_missing_auth_or_claimed_risk_is_not_accepted(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    await configure(db_session, backend)
    anonymous = await client.post(
        "/v1/agents/eplan-assistant/tools/check", json=command(),
    )
    assert anonymous.status_code in (401, 403)
    forged = await client.post(
        "/v1/agents/eplan-assistant/tools/check",
        headers=_headers(),
        json=command(tool_class="read"),
    )
    assert forged.status_code == 400
