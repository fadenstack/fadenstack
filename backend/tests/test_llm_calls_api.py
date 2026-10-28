"""The LLM Calls explorer: filtering, summing up and explaining gateway calls.

The page that replaced the agent-trace graph answers "what went wrong with
this call, and what else was going on": a filtered list, its percentiles, one
call with its tool calls and policy outcomes, the other calls of its
conversation, and where each model name was routed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.services.llm.calls_service import CallFilters, LLMCallsService, _turn_key
from faden_backend.services.llm.graph_service import ProviderOwners

pytestmark = pytest.mark.anyio()

#: The gateway tables the explorer reads, with the columns it reads (see test_deployment_traffic).
_GATEWAY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS llm_provider_instance (
        id           uuid PRIMARY KEY,
        type         varchar(64)  NOT NULL,
        base_url     text         NOT NULL,
        enabled      boolean      NOT NULL DEFAULT true,
        source_kind  varchar(64),
        source_id    varchar(128)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_gateway_request_log (
        id                       uuid PRIMARY KEY,
        request_id               varchar(128) NOT NULL,
        trace_id                 varchar(128),
        tenant_id                varchar(128) NOT NULL,
        user_id                  varchar(128) NOT NULL,
        model_alias              varchar(256),
        provider_instance_id     uuid,
        endpoint                 varchar(128) NOT NULL,
        status_code              integer      NOT NULL,
        latency_ms               integer      NOT NULL DEFAULT 0,
        ttft_ms                  integer,
        prompt_tokens            integer,
        completion_tokens        integer,
        total_tokens             integer,
        cached_tokens            integer,
        error_code               varchar(128),
        estimated_total_cost     numeric(18, 10),
        currency                 varchar(3),
        stream                   boolean,
        session_id               varchar(128),
        agent_id                 varchar(64),
        device_id                varchar(64),
        conversation_id          varchar(128),
        turn_id                  varchar(128),
        group_id                 varchar(128),
        project_id               varchar(128),
        finish_reason            varchar(32),
        retry_count              integer,
        skills_used              json,
        rag_context              json,
        mcp_tool_call_count      integer,
        mcp_tool_loop_iterations integer,
        created_at               timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_tool_call_log (
        id             uuid PRIMARY KEY,
        request_log_id uuid         NOT NULL,
        request_id     varchar(128) NOT NULL,
        iteration    integer      NOT NULL,
        tool_name    varchar(256) NOT NULL,
        mcp_server   varchar(128),
        latency_ms   integer,
        is_error     boolean      NOT NULL DEFAULT false,
        error_kind   varchar(32),
        created_at   timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_gateway_policy_decision (
        id           uuid PRIMARY KEY,
        request_id   varchar(128) NOT NULL,
        tenant_id    varchar(128) NOT NULL,
        user_id      varchar(128) NOT NULL,
        stage        varchar(16)  NOT NULL,
        outcome      varchar(32)  NOT NULL,
        category     varchar(64)  NOT NULL,
        "check"      varchar(128) NOT NULL,
        tool_id      varchar(256),
        policy_name  varchar(256),
        content_hmac varchar(64),
        created_at   timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_client_event (
        id              uuid PRIMARY KEY,
        tenant_id       varchar(128) NOT NULL,
        agent_id        varchar(64)  NOT NULL,
        user_id         varchar(128) NOT NULL,
        device_id       varchar(64),
        conversation_id varchar(128),
        type            varchar(64)  NOT NULL,
        data            json         NOT NULL,
        occurred_at     timestamptz,
        created_at      timestamptz  NOT NULL DEFAULT now()
    )
    """,
)


@pytest.fixture()
async def gateway(dbsession: AsyncSession) -> AsyncSession:
    for statement in _GATEWAY_SCHEMA:
        await dbsession.execute(text(statement))
    await dbsession.flush()
    return dbsession


def _factory(session: AsyncSession) -> Any:
    @asynccontextmanager
    async def same() -> AsyncGenerator[AsyncSession]:
        yield session

    return same


#: The log row id of each call made here, by request id (the explorer opens calls by row).
ROW_IDS: dict[str, str] = {}


async def _call(session: AsyncSession, **values: Any) -> str:
    age = values.pop("age_sec", 60)
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "request_id": str(uuid.uuid4()),
        "tenant_id": "default",
        "user_id": "u1",
        "model_alias": "qwen",
        "endpoint": "/v1/chat/completions",
        "status_code": 200,
        "latency_ms": 1000,
        "created_at": datetime.now(tz=UTC) - timedelta(seconds=age),
    }
    row.update(values)
    columns = ", ".join(f'"{c}"' for c in row)
    await session.execute(
        text(f"INSERT INTO llm_gateway_request_log ({columns}) VALUES ({', '.join(':' + c for c in row)})"),  # noqa: S608
        row,
    )
    ROW_IDS[str(row["request_id"])] = str(row["id"])
    return str(row["request_id"])


def _window() -> CallFilters:
    now = datetime.now(tz=UTC)
    return CallFilters(start=now - timedelta(hours=1), end=now + timedelta(seconds=1))


async def test_list_filters_outcome_model_search_and_sorts(gateway: AsyncSession) -> None:
    ok = await _call(gateway, latency_ms=500, age_sec=30)
    slow = await _call(gateway, latency_ms=900_000, status_code=502, error_code="server_error", age_sec=20)
    other = await _call(gateway, model_alias="tiny", latency_ms=50, age_sec=10, conversation_id="conv-1")
    await _call(gateway, age_sec=7200)  # outside the window

    svc = LLMCallsService(_factory(gateway))
    page = await svc.list_calls(_window())
    assert page["total"] == 3
    assert [c["request_id"] for c in page["items"]] == [other, slow, ok], "newest first"

    errors = await svc.list_calls(CallFilters(**{**_window().__dict__, "outcome": "error"}))
    assert [c["request_id"] for c in errors["items"]] == [slow]
    healthy = await svc.list_calls(CallFilters(**{**_window().__dict__, "outcome": "ok"}))
    assert {c["request_id"] for c in healthy["items"]} == {ok, other}

    by_model = await svc.list_calls(CallFilters(**{**_window().__dict__, "model_alias": "tiny"}))
    assert [c["request_id"] for c in by_model["items"]] == [other]

    by_id = await svc.list_calls(CallFilters(**{**_window().__dict__, "q": slow}))
    assert [c["request_id"] for c in by_id["items"]] == [slow]
    by_error = await svc.list_calls(CallFilters(**{**_window().__dict__, "q": "SERVER_err"}))
    assert [c["request_id"] for c in by_error["items"]] == [slow]
    by_conversation = await svc.list_calls(CallFilters(**{**_window().__dict__, "session": "conv-1"}))
    assert [c["request_id"] for c in by_conversation["items"]] == [other]
    wildcard = await svc.list_calls(CallFilters(**{**_window().__dict__, "q": "%"}))
    assert wildcard["total"] == 0, "a % in the search is a character, not a wildcard"
    by_person = await svc.list_calls(CallFilters(**{**_window().__dict__, "q": "u1"}))
    assert by_person["total"] == 0, "nobody is searched for by their id (M02)"
    assert all(c["user_id"] is None for c in page["items"]), "nobody is named"

    slowest = await svc.list_calls(_window(), sort="slowest", limit=1)
    assert slowest["total"] == 3
    assert [c["request_id"] for c in slowest["items"]] == [slow]


async def test_stats_sum_up_and_keep_every_model_to_choose_from(gateway: AsyncSession) -> None:
    for ms in (100, 200, 300, 400):
        await _call(gateway, latency_ms=ms, prompt_tokens=10, completion_tokens=5, ttft_ms=ms // 2)
    await _call(gateway, model_alias="tiny", status_code=502, latency_ms=10_000)

    svc = LLMCallsService(_factory(gateway))
    stats = await svc.stats(_window())
    assert stats["total"] == 5
    assert stats["errors"] == 1
    assert stats["error_rate"] == pytest.approx(0.2)
    assert stats["p50_latency_ms"] == pytest.approx(300)
    assert stats["prompt_tokens"] == 40
    assert stats["completion_tokens"] == 20
    assert stats["models"] == [{"model_alias": "qwen", "calls": 4}, {"model_alias": "tiny", "calls": 1}]

    narrowed = await svc.stats(CallFilters(**{**_window().__dict__, "model_alias": "tiny"}))
    assert narrowed["total"] == 1
    assert len(narrowed["models"]) == 2, "the model list ignores the model filter"


async def _tool(session: AsyncSession, rid: str, iteration: int, name: str, *, failed: bool) -> None:
    await session.execute(
        text(
            "INSERT INTO llm_tool_call_log (id, request_log_id, request_id, iteration, tool_name, latency_ms,"
            " is_error, error_kind) VALUES (:id, :row, :rid, :it, :name, 120, :err, :kind)",
        ),
        {"id": uuid.uuid4(), "row": uuid.UUID(ROW_IDS[rid]), "rid": rid, "it": iteration, "name": name,
         "err": failed, "kind": "timeout" if failed else None},
    )


async def _policy(session: AsyncSession, rid: str, user: str = "u1") -> None:
    await session.execute(
        text(
            "INSERT INTO llm_gateway_policy_decision (id, request_id, tenant_id, user_id, stage, outcome, category,"
            ' "check", policy_name, content_hmac)'
            " VALUES (:id, :rid, 'default', :user, 'input', 'block', 'pii', 'pii.email', 'secret', 'abc')",
        ),
        {"id": uuid.uuid4(), "rid": rid, "user": user},
    )


async def test_detail_brings_tools_policies_and_the_conversation(gateway: AsyncSession) -> None:
    first = await _call(gateway, conversation_id="conv-9", age_sec=40)
    second = await _call(gateway, conversation_id="conv-9", age_sec=20, mcp_tool_call_count=2)
    await _call(gateway, conversation_id="conv-other", age_sec=10)
    await _tool(gateway, second, 0, "search", failed=False)
    await _tool(gateway, second, 1, "fetch", failed=True)
    await _policy(gateway, second)

    svc = LLMCallsService(_factory(gateway))
    detail = await svc.detail(ROW_IDS[second])
    assert detail is not None
    assert detail["call"]["request_id"] == second
    assert [t["tool_name"] for t in detail["tool_calls"]] == ["search", "fetch"]
    assert detail["tool_calls"][1]["error_kind"] == "timeout"
    assert detail["policy_decisions"] == [
        {"stage": "input", "outcome": "block", "category": "pii", "check_name": "pii.email",
         "tool_id": None, "created_at": detail["policy_decisions"][0]["created_at"]},
    ], "never the policy's name or the hash of the refused text"
    assert detail["related_by"] == "conversation_id"
    assert [c["request_id"] for c in detail["related"]] == [first, second]

    alone = await svc.detail(ROW_IDS[await _call(gateway)])
    assert alone is not None
    assert alone["related"] == [], "a call alone has nothing related"
    assert await svc.detail("nope") is None
    assert await svc.detail(str(uuid.uuid4())) is None


async def test_a_shared_request_id_does_not_mix_calls(gateway: AsyncSession) -> None:
    """A caller chooses the request id (X-Request-ID): another user's call may carry the same one."""
    mine = await _call(gateway, request_id="same", user_id="u1", age_sec=60)
    mine_row = ROW_IDS[mine]
    await _call(gateway, request_id="same", user_id="u2", age_sec=10)
    theirs_row = ROW_IDS["same"]
    await _tool(gateway, "same", 0, "their_tool", failed=True)  # on the newer (their) row
    await _policy(gateway, "same", user="u2")

    svc = LLMCallsService(_factory(gateway))
    detail = await svc.detail(mine_row)
    assert detail is not None
    assert detail["call"]["id"] == mine_row, "the row asked for, not the newest with that request id"
    assert detail["call"]["user_id"] is None, "nobody is named"
    assert detail["tool_calls"] == []
    assert detail["policy_decisions"] == []

    theirs = await svc.detail(theirs_row)
    assert theirs is not None
    assert [t["tool_name"] for t in theirs["tool_calls"]] == ["their_tool"]
    assert len(theirs["policy_decisions"]) == 1


async def test_routing_names_the_provider_each_instance_serves(gateway: AsyncSession) -> None:
    instance, deployment, provider = uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
    await gateway.execute(
        text(
            "INSERT INTO llm_provider_instance (id, type, base_url, source_kind, source_id)"
            " VALUES (:id, 'vllm', 'http://x', 'inference_deployment', :src)",
        ),
        {"id": instance, "src": str(deployment)},
    )
    await _call(gateway, provider_instance_id=instance, total_tokens=10)
    await _call(gateway, provider_instance_id=instance, status_code=502, total_tokens=None)
    await _call(gateway, model_alias="gone", provider_instance_id=uuid.uuid4())

    owners = ProviderOwners(
        runtimes=[],
        providers=[SimpleNamespace(id=provider, source_kind="inference_deployment", source_id=str(deployment))],
    )
    svc = LLMCallsService(_factory(gateway), owners=owners, provider_names={provider: "qwen-cluster"})
    routes = {r["model_alias"]: r for r in (await svc.routing(_window()))["routes"]}
    assert routes["qwen"]["calls"] == 2
    assert routes["qwen"]["errors"] == 1
    assert routes["qwen"]["tokens"] == 10
    assert routes["qwen"]["provider_id"] == provider
    assert routes["qwen"]["provider_name"] == "qwen-cluster"
    assert routes["gone"]["provider_id"] is None, "an instance whose provider is gone stays unnamed"

    page = await svc.list_calls(_window())
    named = [c for c in page["items"] if c["model_alias"] == "qwen"]
    assert all(c["provider_name"] == "qwen-cluster" for c in named)


async def _event(session: AsyncSession, conv: str, kind: str, age_sec: float, user: str = "u1", **data: Any) -> None:
    await session.execute(
        text(
            "INSERT INTO agent_client_event (id, tenant_id, agent_id, user_id, conversation_id, type, data, occurred_at)"
            " VALUES (:id, 'default', 'a1', :user, :conv, :type, CAST(:data AS json), :at)",
        ),
        {"id": uuid.uuid4(), "user": user, "conv": conv, "type": kind, "data": json.dumps(data),
         "at": datetime.now(tz=UTC) - timedelta(seconds=age_sec)},
    )


async def test_turns_group_an_agents_rounds_with_the_tools_its_app_ran(gateway: AsyncSession) -> None:
    """An agent's app asks, runs the tools the model asked for on the device, and asks again: one turn."""
    agent = {"agent_id": "a1", "conversation_id": "c1", "turn_id": "t1", "latency_ms": 2000}
    await _call(gateway, **agent, finish_reason="tool_calls", age_sec=50)
    second = await _call(gateway, **agent, finish_reason="tool_calls", age_sec=40)
    last = await _call(gateway, **agent, finish_reason="stop", age_sec=30, prompt_tokens=10, total_tokens=12)
    await _event(gateway, "c1", "tool.executed", 45, tool="read_context", source="host", outcome="ok",
                 duration_ms=13, arguments="never shown")
    await _event(gateway, "c1", "approval.decided", 36, tool="insert_text", decision="denied", wait_ms=2000)
    await _event(gateway, "c1", "tool.executed", 35, tool="insert_text", source="host", outcome="denied")
    await _event(gateway, "c1", "tool.executed", 5, tool="later", outcome="ok")  # a later turn's
    await _event(gateway, "c2", "tool.executed", 45, tool="elsewhere", outcome="ok")
    lone = await _call(gateway, age_sec=20)  # a call of no turn: a turn of its own
    await _call(gateway, user_id="u2", agent_id="a1", turn_id="t1", age_sec=10)  # same turn id, another user

    svc = LLMCallsService(_factory(gateway))
    page = await svc.list_turns(_window())
    assert page["total"] == 3
    assert all(t["user_id"] is None and "u1" not in t["key"] for t in page["items"]), "nobody is named"
    turns = {t["key"]: t for t in page["items"]}
    turn = turns[_turn_key("u1", "t1")]
    assert turn["calls"] == 3
    assert turn["tool_rounds"] == 2
    assert turn["finish_reason"] == "stop"
    assert turn["last_call_id"] == ROW_IDS[last]
    assert turn["llm_ms"] == 6000
    assert turn["duration_ms"] >= 21_000, "from the first call's start to the last call's end"
    assert (turn["client_tools"], turn["client_tool_errors"]) == (2, 1)
    assert (turn["approvals"], turn["approvals_denied"], turn["approval_wait_ms"]) == (1, 1, 2000)
    assert turns[_turn_key("u2", "t1")]["calls"] == 1, "a turn id is the app's: another user's is another turn"
    assert turns[_turn_key("u1", ROW_IDS[lone])]["calls"] == 1
    assert next(t["turn_id"] for t in page["items"]) == "t1", "newest first: u2's call"

    errors_only = await svc.list_turns(CallFilters(**{**_window().__dict__, "model_alias": "nope"}))
    assert errors_only["total"] == 0

    detail = await svc.detail(ROW_IDS[second])
    assert detail is not None
    assert [c["finish_reason"] for c in detail["turn_calls"]] == ["tool_calls", "tool_calls", "stop"]
    assert [(e["type"], e["tool"]) for e in detail["client_events"]] == [
        ("tool.executed", "read_context"), ("approval.decided", "insert_text"), ("tool.executed", "insert_text"),
    ]
    assert all("arguments" not in e and "user_id" not in e for e in detail["client_events"])
    alone = await svc.detail(ROW_IDS[await _call(gateway)])
    assert alone is not None
    assert [c["id"] for c in alone["turn_calls"]] == [alone["call"]["id"]]
    assert alone["client_events"] == []


async def test_without_gateway_database_the_explorer_is_empty() -> None:
    svc = LLMCallsService(None)
    assert (await svc.list_calls(_window()))["items"] == []
    assert (await svc.list_turns(_window()))["items"] == []
    assert (await svc.stats(_window()))["total"] == 0
    assert await svc.detail("x") is None
    assert (await svc.routing(_window()))["routes"] == []


async def test_calls_api_validates_range_and_needs_permission(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
) -> None:
    from unittest.mock import MagicMock

    from faden_backend.db.models.users import User, current_active_user
    from faden_backend.web.api.llm.dependencies import get_llm_calls_service

    rid = await _call(gateway, model_alias="qwen")
    fastapi_app.dependency_overrides[get_llm_calls_service] = lambda: LLMCallsService(_factory(gateway))

    def as_user(superuser: bool) -> None:
        user = MagicMock(spec=User)
        user.id = uuid.uuid4()
        user.is_active = True
        user.is_superuser = superuser
        user.is_verified = True
        fastapi_app.dependency_overrides[current_active_user] = lambda: user

    as_user(superuser=False)
    assert (await client.get(fastapi_app.url_path_for("list_llm_calls"))).status_code == 403

    as_user(superuser=True)
    listed = await client.get(fastapi_app.url_path_for("list_llm_calls"), params={"outcome": "ok"})
    assert listed.status_code == 200
    assert [c["request_id"] for c in listed.json()["items"]] == [rid]

    turns = await client.get(fastapi_app.url_path_for("list_llm_turns"))
    assert [t["last_call_id"] for t in turns.json()["items"]] == [ROW_IDS[rid]]

    stats = await client.get(fastapi_app.url_path_for("get_llm_call_stats"))
    assert stats.json()["total"] == 1

    detail = await client.get(fastapi_app.url_path_for("get_llm_call", call_id=ROW_IDS[rid]))
    assert detail.status_code == 200
    assert detail.json()["call"]["model_alias"] == "qwen"
    missing = await client.get(fastapi_app.url_path_for("get_llm_call", call_id="nope"))
    assert missing.status_code == 404

    routing = await client.get(fastapi_app.url_path_for("get_llm_routing"))
    assert routing.json()["routes"][0]["calls"] == 1

    now = datetime.now(tz=UTC)
    too_long = await client.get(
        fastapi_app.url_path_for("list_llm_calls"),
        params={"start": (now - timedelta(days=120)).isoformat(), "end": now.isoformat()},
    )
    assert too_long.status_code == 400
    backwards = await client.get(
        fastapi_app.url_path_for("list_llm_calls"),
        params={"start": now.isoformat(), "end": (now - timedelta(hours=1)).isoformat()},
    )
    assert backwards.status_code == 400
