"""The device channel (``/v1/client/ws``): who may connect, and calls that arrive.

A device keeps a WebSocket open and runs tools for its user. It used to take
anyone, keep devices per worker only, give a person 30 s to approve, and drop
calls when the connection blinked.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from faden_gateway.services.gateway.client_ws import (
    DeviceHub,
    DeviceIdentity,
    broker_tool_call,
    handle_client_websocket,
)
from faden_gateway.web.api.v1.devices import authenticate_device
from tests.test_agent_routes import (  # noqa: F401
    AGENT_ID,
    USER,
    _agent_token,
    _Backend,
    _login_token,
    _profile,
    backend,
)

ME = DeviceIdentity(tenant_id="tenant-a", user_id=USER, device_id=None, agent_id=None)
SESSION = uuid.uuid4()
GOOD = "a-valid-token"  # what _accept_all accepts


class _ClosedError(Exception):
    """The device went away."""


class _Socket:
    """A WebSocket as ``handle_client_websocket`` sees it; the test plays the device."""

    def __init__(self, *, token: str | None = None) -> None:
        self.headers = {"authorization": f"Bearer {token}"} if token else {}
        self.scope: dict[str, Any] = {"subprotocols": []}
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.accepted = False
        self.closed: int | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed = code
        self.inbox.put_nowait(None)

    async def send_json(self, message: dict[str, Any]) -> None:
        self.outbox.put_nowait(message)

    async def receive_text(self) -> str:
        item = await self.inbox.get()
        if item is None:
            raise _ClosedError
        return item

    def say(self, **message: Any) -> None:
        self.inbox.put_nowait(json.dumps(message))

    async def heard(self, timeout: float = 2.0) -> dict[str, Any]:
        return await asyncio.wait_for(self.outbox.get(), timeout)


class _Dao:
    def __init__(self) -> None:
        self.registered: list[tuple[uuid.UUID, str, int]] = []
        self.unavailable: list[tuple[uuid.UUID, str]] = []

    async def register_client_capabilities(self, session_id: uuid.UUID, client_id: str, tools: list[Any]) -> None:
        self.registered.append((session_id, client_id, len(tools)))

    async def mark_client_tools_unavailable(self, session_id: uuid.UUID, client_id: str) -> None:
        self.unavailable.append((session_id, client_id))


async def _accept_all(token: str) -> DeviceIdentity:
    if token != GOOD:
        raise PermissionError("invalid_token", "Invalid JWT token.")
    return ME


async def _owns(identity: DeviceIdentity, session_id: uuid.UUID) -> bool:
    return session_id == SESSION


async def _connect(
    hub: DeviceHub, *, client_id: str = "laptop", session: uuid.UUID | None = SESSION,
    identity: DeviceIdentity = ME, dao: _Dao | None = None,
) -> tuple[_Socket, asyncio.Task[None]]:
    ws = _Socket()

    async def authenticate(token: str) -> DeviceIdentity:
        return identity

    task = asyncio.create_task(handle_client_websocket(
        ws, dao or _Dao(), authenticate=authenticate, owns_session=_owns, hub=hub,
    ))
    ws.say(type="session_init", token=GOOD, client_id=client_id,
           session_id=str(session) if session else None, tools=[{"tool_id": "read_page"}])
    ack = await ws.heard()
    assert ack["type"] == "session_ack", ack
    return ws, task


async def _drop(ws: _Socket, task: asyncio.Task[None]) -> None:
    ws.inbox.put_nowait(None)
    await asyncio.wait_for(task, 2)


def _call(hub: DeviceHub, *, timeout: float = 2.0, session: uuid.UUID | None = SESSION,
          agent_id: str | None = None, call_id: str = "call_1") -> asyncio.Task[Any]:
    return asyncio.create_task(hub.call(
        tenant_id="tenant-a", user_id=USER, session_id=session, tool_id="read_page",
        arguments={"page": 3}, call_id=call_id, agent_id=agent_id, timeout=timeout,
    ))


@pytest.fixture
async def hubs() -> AsyncIterator[tuple[DeviceHub, DeviceHub]]:
    """Two workers, one Redis."""
    server = FakeServer()
    a, b = DeviceHub(worker_id="worker-a"), DeviceHub(worker_id="worker-b")
    await a.start(FakeRedis(server=server))
    await b.start(FakeRedis(server=server))
    yield a, b
    await a.stop()
    await b.stop()


# -- who may connect ---------------------------------------------------------------


@pytest.mark.anyio
async def test_a_socket_without_a_token_registers_nothing() -> None:
    hub, dao, ws = DeviceHub(), _Dao(), _Socket()
    task = asyncio.create_task(handle_client_websocket(ws, dao, authenticate=_accept_all, owns_session=_owns, hub=hub))
    ws.say(type="session_init", client_id="laptop", session_id=str(SESSION), tenant_id="tenant-a",
           tools=[{"tool_id": "read_page"}])
    refused = await ws.heard()
    await asyncio.wait_for(task, 2)
    assert refused["code"] == "missing_authorization"
    assert ws.closed == 4401
    assert dao.registered == [] and hub.get(("tenant-a", USER, "laptop")) is None


@pytest.mark.anyio
async def test_a_bad_token_in_the_handshake_is_refused_before_accepting() -> None:
    hub, dao, ws = DeviceHub(), _Dao(), _Socket(token="forged")  # noqa: S106
    await handle_client_websocket(ws, dao, authenticate=_accept_all, owns_session=_owns, hub=hub)
    assert not ws.accepted and ws.closed == 4401


@pytest.mark.anyio
async def test_another_users_session_is_refused() -> None:
    hub, dao, ws = DeviceHub(), _Dao(), _Socket(token=GOOD)
    task = asyncio.create_task(handle_client_websocket(ws, dao, authenticate=_accept_all, owns_session=_owns, hub=hub))
    ws.say(type="session_init", client_id="laptop", session_id=str(uuid.uuid4()), tools=[])
    assert (await ws.heard())["code"] == "session_not_found"
    await asyncio.wait_for(task, 2)
    assert dao.registered == [] and hub.get(("tenant-a", USER, "laptop")) is None


def test_the_route_refuses_an_unauthenticated_socket(fastapi_app: FastAPI) -> None:
    # No lifespan: refusing needs no database (the real route is run end to end).
    fastapi_app.state.db_session_factory = lambda: pytest.fail("a refused socket touched the database")
    http = TestClient(fastapi_app)
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        http.websocket_connect("/v1/client/ws", headers={"Authorization": "Bearer nonsense"}),
    ):
        pass
    assert refused.value.code == 4401
    with http.websocket_connect("/v1/client/ws", subprotocols=["faden.v1"]) as ws:
        ws.send_text(json.dumps({"type": "session_init", "client_id": "x", "tenant_id": "tenant-a"}))
        assert ws.receive_json()["code"] == "missing_authorization"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


@pytest.mark.anyio
async def test_an_agent_token_signs_in_its_device_while_the_agent_lets_the_user_in(
    fastapi_app: FastAPI, backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    who = await authenticate_device(fastapi_app, _agent_token())
    assert who == DeviceIdentity(tenant_id="tenant-a", user_id=USER, device_id="device-7", agent_id=AGENT_ID)
    assert (await authenticate_device(fastapi_app, _login_token(USER))).agent_id is None

    backend.put(_profile(grant_users=frozenset()))
    fastapi_app.state.agent_directory.invalidate()
    with pytest.raises(PermissionError) as refused:
        await authenticate_device(fastapi_app, _agent_token())
    assert refused.value.args[0] == "agent_not_granted"
    with pytest.raises(PermissionError) as forged:
        await authenticate_device(fastapi_app, "not.a.token")
    assert forged.value.args[0] == "invalid_token"


# -- calls ---------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_device_answers_a_call() -> None:
    hub = DeviceHub()
    ws, task = await _connect(hub)
    call = _call(hub)
    request = await ws.heard()
    assert request == {"type": "tool_call_request", "call_id": "call_1", "session_id": str(SESSION),
                       "tool_id": "read_page", "arguments": {"page": 3}}
    ws.say(type="tool_call_result", call_id="call_1", tool_id="read_page", status="ok", result={"content": "Page 3"})
    result = await call
    assert (result.content, result.is_error) == ("Page 3", False)
    await _drop(ws, task)


@pytest.mark.anyio
async def test_one_device_serves_the_users_other_sessions_but_not_other_users() -> None:
    hub = DeviceHub()
    ws, task = await _connect(hub, session=None)
    call = _call(hub, session=uuid.uuid4())
    assert (await ws.heard())["call_id"] == "call_1"
    ws.say(type="tool_call_result", call_id="call_1", status="ok", result="done")
    assert (await call).content == "done"

    stranger = await hub.call(tenant_id="tenant-a", user_id="someone-else", session_id=uuid.uuid4(),
                              tool_id="read_page", arguments={}, call_id="call_2", timeout=1)
    assert stranger.is_error and "No device" in stranger.content
    await _drop(ws, task)


@pytest.mark.anyio
async def test_an_agents_device_serves_only_that_agents_calls() -> None:
    hub = DeviceHub()
    agent_device = DeviceIdentity(tenant_id="tenant-a", user_id=USER, device_id="device-7", agent_id=AGENT_ID)
    ws, task = await _connect(hub, session=None, identity=agent_device)
    chat = await hub.call(tenant_id="tenant-a", user_id=USER, session_id=uuid.uuid4(), tool_id="read_page",
                          arguments={}, call_id="call_1", timeout=1)
    assert chat.is_error, "a chat's call never reaches an agent's device"
    call = _call(hub, session=None, agent_id=AGENT_ID, call_id="call_2")
    assert (await ws.heard())["call_id"] == "call_2"
    ws.say(type="tool_call_result", call_id="call_2", status="ok", result="ok")
    assert (await call).content == "ok"
    await _drop(ws, task)


@pytest.mark.anyio
async def test_a_device_cannot_answer_a_call_it_was_not_given() -> None:
    hub = DeviceHub()
    honest, task_1 = await _connect(hub, client_id="laptop")
    forger, task_2 = await _connect(hub, client_id="other", session=None)
    call = _call(hub)
    await honest.heard()
    forger.say(type="tool_call_result", call_id="call_1", status="ok", result="forged")
    forger.say(type="ping")
    assert (await forger.heard())["type"] == "pong"
    assert not call.done()
    honest.say(type="tool_call_result", call_id="call_1", status="ok", result="real")
    assert (await call).content == "real"
    await _drop(honest, task_1)
    await _drop(forger, task_2)


@pytest.mark.anyio
async def test_a_call_to_a_device_on_another_worker(hubs: tuple[DeviceHub, DeviceHub]) -> None:
    a, b = hubs
    ws, task = await _connect(b)  # the socket is on worker B
    call = _call(a)               # the request is on worker A
    request = await ws.heard()
    assert request["call_id"] == "call_1" and request["arguments"] == {"page": 3}
    ws.say(type="tool_call_result", call_id="call_1", status="ok", result="from B")
    result = await call
    assert (result.content, result.is_error) == ("from B", False)

    unbound = _call(a, session=None, call_id="call_2")  # by user, not session
    assert (await ws.heard())["call_id"] == "call_2"
    ws.say(type="tool_call_error", call_id="call_2", error="Page not found")
    failed = await unbound
    assert failed.is_error and failed.content == "Page not found"
    await _drop(ws, task)


@pytest.mark.anyio
@pytest.mark.parametrize("across", [False, True], ids=["same worker", "across workers"])
async def test_waiting_for_a_person_extends_the_deadline(across: bool, hubs: tuple[DeviceHub, DeviceHub]) -> None:
    a, b = hubs
    ws, task = await _connect(b)
    call = _call(a if across else b, timeout=0.3)
    await ws.heard()
    ws.say(type="tool_call_approval_pending", call_id="call_1", timeout_s=5)
    await asyncio.sleep(0.6)  # twice the timeout
    assert not call.done(), "still waiting for the person"
    ws.say(type="tool_call_result", call_id="call_1", status="ok", result="approved and done")
    assert (await call).content == "approved and done"
    await _drop(ws, task)


@pytest.mark.anyio
@pytest.mark.parametrize("across", [False, True], ids=["same worker", "across workers"])
async def test_a_call_that_times_out_is_cancelled_on_the_device(
    across: bool, hubs: tuple[DeviceHub, DeviceHub],
) -> None:
    a, b = hubs
    ws, task = await _connect(b)
    result = await asyncio.gather(_call(a if across else b, timeout=0.2), ws.heard())
    assert result[1]["type"] == "tool_call_request"
    assert result[0].is_error and "did not answer in time" in result[0].content
    assert await ws.heard() == {"type": "tool_call_cancel", "call_id": "call_1"}
    await _drop(ws, task)


@pytest.mark.anyio
async def test_a_device_that_reconnects_in_time_gets_its_calls_again() -> None:
    hub = DeviceHub()
    dao = _Dao()
    ws, task = await _connect(hub, dao=dao)
    call = _call(hub)
    await ws.heard()
    await _drop(ws, task)
    assert dao.unavailable == [(SESSION, "laptop")]

    again, task = await _connect(hub, dao=dao)
    request = await again.heard()
    assert request["call_id"] == "call_1", "the pending call is sent again"
    again.say(type="tool_call_result", call_id="call_1", status="ok", result="after reconnect")
    assert (await call).content == "after reconnect"
    await _drop(again, task)


@pytest.mark.anyio
async def test_a_device_that_does_not_come_back_fails_its_calls_clearly() -> None:
    hub = DeviceHub()
    hub.grace_sec = 0.1
    ws, task = await _connect(hub)
    call = _call(hub, timeout=5)
    await ws.heard()
    await _drop(ws, task)
    result = await asyncio.wait_for(call, 2)
    assert result.is_error and "disconnected" in result.content


@pytest.mark.anyio
async def test_the_broker_entry_point_reaches_the_users_device() -> None:
    hub = DeviceHub()
    ws, task = await _connect(hub)
    call = asyncio.create_task(broker_tool_call(
        session_id=SESSION, tool_id="read_page", arguments={}, call_id="c", tenant_id="tenant-a",
        user_id=USER, hub=hub,
    ))
    await ws.heard()
    ws.say(type="tool_call_result", call_id="c", status="ok", result="ok")
    assert (await call).content == "ok"
    await _drop(ws, task)


@pytest.mark.anyio
@pytest.mark.parametrize("across", [False, True], ids=["same worker", "across workers"])
async def test_a_call_that_needs_approval_asks_the_device_to_ask_its_user(
    across: bool, hubs: tuple[DeviceHub, DeviceHub],
) -> None:
    a, b = hubs
    ws, task = await _connect(b)
    call = asyncio.create_task((a if across else b).call(
        tenant_id="tenant-a", user_id=USER, session_id=SESSION, tool_id="read_page", arguments={},
        call_id="call_1", approval="Reading pages needs your approval.", timeout=0.3,
    ))
    request = await ws.heard()
    assert request["approval_required"] == {"reason": "Reading pages needs your approval."}
    ws.say(type="tool_call_approval_pending", call_id="call_1", timeout_s=5)
    await asyncio.sleep(0.5)
    ws.say(type="tool_call_result", call_id="call_1", status="ok", result="read")
    assert (await call).content == "read"
    await _drop(ws, task)


# -- while it stays connected ------------------------------------------------------


class _Verdicts:
    """An authenticator whose answer changes while the device is connected."""

    def __init__(self) -> None:
        self.refusal: tuple[str, str] | None = None
        self.as_whom: dict[str, DeviceIdentity] = {GOOD: ME}
        self.seen: list[str] = []

    async def __call__(self, token: str) -> DeviceIdentity:
        self.seen.append(token)
        if self.refusal is not None:
            raise PermissionError(*self.refusal)
        if token not in self.as_whom:
            raise PermissionError("invalid_token", "Invalid JWT token.")
        return self.as_whom[token]


async def _connect_with(hub: DeviceHub, verdicts: _Verdicts) -> tuple[_Socket, asyncio.Task[None]]:
    ws = _Socket()
    task = asyncio.create_task(handle_client_websocket(
        ws, _Dao(), authenticate=verdicts, owns_session=_owns, hub=hub,
    ))
    ws.say(type="session_init", token=GOOD, client_id="laptop", session_id=str(SESSION), tools=[])
    assert (await ws.heard())["type"] == "session_ack"
    return ws, task


@pytest.fixture
def quick_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("faden_gateway.services.gateway.client_ws.RECHECK_SEC", 0.02)


@pytest.mark.anyio
@pytest.mark.parametrize(("code", "closed"), [("api_token_revoked", 4401), ("user_inactive", 4403)])
async def test_a_device_is_let_go_once_its_caller_may_no_longer(code: str, closed: int, quick_recheck: None) -> None:
    """A revoked token, a suspended person: told why, and the channel closes -- not hours later."""
    hub = DeviceHub()
    verdicts = _Verdicts()
    ws, task = await _connect_with(hub, verdicts)
    verdicts.refusal = (code, "Refused.")

    told = await ws.heard()
    assert told == {"type": "error", "code": code, "message": "Refused."}
    await asyncio.wait_for(task, 2)
    assert ws.closed == closed


@pytest.mark.anyio
async def test_a_check_that_could_not_be_made_keeps_the_device(quick_recheck: None) -> None:
    hub = DeviceHub()
    verdicts = _Verdicts()
    ws, task = await _connect_with(hub, verdicts)
    verdicts.refusal = ("access_check_failed", "Your access could not be checked; try again.")
    await asyncio.sleep(0.1)
    assert ws.closed is None and ws.outbox.empty()
    verdicts.refusal = None
    await _drop(ws, task)


@pytest.mark.anyio
async def test_a_device_renews_its_token_in_place(quick_recheck: None) -> None:
    hub = DeviceHub()
    verdicts = _Verdicts()
    verdicts.as_whom["renewed"] = ME
    ws, task = await _connect_with(hub, verdicts)

    ws.say(type="token_refresh", token="renewed")
    assert await ws.heard() == {"type": "token_refreshed"}
    del verdicts.as_whom[GOOD]  # the old token is no good any more; the new one carries on
    await asyncio.sleep(0.1)
    assert ws.closed is None and verdicts.seen[-1] == "renewed"
    await _drop(ws, task)


@pytest.mark.anyio
async def test_a_token_of_someone_else_does_not_take_over_a_device() -> None:
    hub = DeviceHub()
    verdicts = _Verdicts()
    verdicts.as_whom["someone-else"] = DeviceIdentity(
        tenant_id="tenant-a", user_id="another-user", device_id=None, agent_id=None,
    )
    ws, task = await _connect_with(hub, verdicts)

    ws.say(type="token_refresh", token="someone-else")
    assert (await ws.heard())["code"] == "token_mismatch"
    await asyncio.wait_for(task, 2)
    assert ws.closed == 4403
