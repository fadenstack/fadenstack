"""The device channel: tools that run on a user's machine, called from the gateway.

A device -- an SDK integration, the desktop agent, a local client -- keeps a
WebSocket open to ``/v1/client/ws`` and offers its tools; the gateway calls
them for that user's requests.

What changed from the first version, and why:

- **Authentication first.** The channel took anyone: a ``session_init`` naming
  any session and tenant registered tools there and received that session's
  tool calls. Now nothing is registered until a token is verified -- an agent
  token (its agent's access rules apply, its ``did`` is the device) or a
  user's token -- sent in the upgrade's ``Authorization`` header, as the
  WebSocket subprotocol ``faden.bearer.<token>`` (browsers cannot set
  headers), or in ``session_init``. A session named in ``session_init`` must be
  the user's own.
- **Per device, not per session.** A device registers once, keyed by tenant,
  user and device; any session of that user can use it. Naming a session
  still binds the device to it, first choice for that session's calls.
- **Across workers.** The registry was in memory, so a call handled by another
  worker or replica found no device. With Redis, each worker announces its
  devices and listens on a channel of its own; a call for a device elsewhere
  goes to the worker holding the socket and the answer comes back the same
  way.
- **Time for people.** 30 s was too short for a human approval. The device
  says ``tool_call_progress`` (another stretch) or
  ``tool_call_approval_pending`` (up to the approval timeout) and the wait
  grows; a call that times out is cancelled on the device.
- **Reconnects.** A device that drops keeps its pending calls for a grace
  period; reconnecting as the same device gets them again. After it, they
  fail with a clear error.
- **Checked while connected.** The token was checked once, at connect: a
  revoked or expired token, a suspended person, an agent whose access was
  taken away went on getting calls for hours. The caller is checked again
  every ``RECHECK_SEC``; when it may no longer be served, the device gets an
  ``error`` saying why and the channel closes (4401: the token is no good,
  4403: the caller may not). A device renews its token in place with
  ``token_refresh`` -- an agent's lasts minutes -- as the same caller only.

Protocol (JSON text frames):

  device -> gateway: session_init, tool_call_result, tool_call_error,
                     tool_call_progress, tool_call_approval_pending,
                     tool_registry_update, token_refresh, ping
  gateway -> device: session_ack, tool_call_request, tool_call_cancel,
                     tool_registry_update_ack, token_refreshed, pong, error
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.services.gateway.tool_router import ToolCallResult

logger = logging.getLogger(__name__)

#: How long a call waits for a device, unless the device asks for more.
TOOL_CALL_TIMEOUT_SEC = 30.0
#: The longest a call waits once the device says a person has to approve it.
APPROVAL_TIMEOUT_SEC = 600.0
#: How long a dropped device's pending calls wait for it to come back.
RECONNECT_GRACE_SEC = 15.0
#: How long a connection has to authenticate.
AUTH_TIMEOUT_SEC = 10.0
#: Presence in Redis lives this long and is renewed a third of the way.
PRESENCE_TTL_SEC = 90
SUBPROTOCOL = "faden.v1"
BEARER_PREFIX = "faden.bearer."

DeviceKey = tuple[str, str, str]  # (tenant_id, user_id, device_id)


def _key_str(key: DeviceKey) -> str:
    return ":".join(key)


def _key_of(value: str) -> DeviceKey:
    tenant, user, device = value.split(":", 2)
    return tenant, user, device


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _presence(value: str | bytes) -> tuple[str, str | None]:
    """``(worker, agent)`` of a device's presence in Redis."""
    body = json.loads(_text(value))
    return str(body["worker"]), body.get("agent")


def _failed(call_id: str, tool_id: str, message: str, started: float | None = None) -> ToolCallResult:
    return ToolCallResult(
        call_id=call_id,
        tool_id=tool_id,
        content=message,
        is_error=True,
        latency_ms=int((time.monotonic() - started) * 1000) if started else 0,
        realm="client_local",
        executor="ClientToolBroker",
    )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class Waiting:
    """A call this worker waits on, for the request that made it."""

    call_id: str
    tool_id: str
    future: asyncio.Future[ToolCallResult]
    deadline: float
    started: float = field(default_factory=time.monotonic)
    #: Where the call went: a local device, or another worker's.
    device: DeviceKey | None = None
    worker: str | None = None


@dataclass
class InFlight:
    """A call sent to a device on this worker; answered to *reply_to* (a worker) or here."""

    call_id: str
    tool_id: str
    arguments: dict[str, Any]
    reply_to: str | None = None
    #: Why the user must approve the call first; the device asks them.
    approval: str | None = None


@dataclass
class DeviceConnection:
    """One authenticated device, connected to this worker."""

    key: DeviceKey
    websocket: Any
    tools: list[dict[str, Any]] = field(default_factory=list)
    session_id: uuid.UUID | None = None
    agent_id: str | None = None
    inflight: dict[str, InFlight] = field(default_factory=dict)
    connected: bool = True

    def offers(self, tool_id: str) -> bool:
        return not self.tools or any(t.get("tool_id") == tool_id for t in self.tools)

    async def send(self, message: dict[str, Any]) -> None:
        await self.websocket.send_json(message)


# ---------------------------------------------------------------------------
# The hub
# ---------------------------------------------------------------------------


class DeviceHub:
    """The devices connected to this worker, and the way to the others'."""

    def __init__(self, *, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or uuid.uuid4().hex
        self._devices: dict[DeviceKey, DeviceConnection] = {}
        self._sessions: dict[uuid.UUID, DeviceKey] = {}
        self._waiting: dict[str, Waiting] = {}
        self._orphans: dict[DeviceKey, dict[str, InFlight]] = {}
        self._orphan_timers: dict[DeviceKey, asyncio.Task[None]] = {}
        self._redis: Any = None
        self._tasks: list[asyncio.Task[None]] = []
        self.grace_sec = RECONNECT_GRACE_SEC

    # -- lifecycle ----------------------------------------------------------------

    async def start(self, redis: Any | None) -> None:
        """Listen on this worker's channel and keep its devices announced (with Redis)."""
        self._redis = redis
        if redis is None:
            return
        ready = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._listen(ready)),
            asyncio.create_task(self._announce()),
        ]
        await asyncio.wait_for(ready.wait(), timeout=5)

    async def stop(self) -> None:
        for task in [*self._tasks, *self._orphan_timers.values()]:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self._orphan_timers = {}
        self._redis = None

    # -- devices ------------------------------------------------------------------------

    def get(self, key: DeviceKey) -> DeviceConnection | None:
        return self._devices.get(key)

    def device_for_session(self, session_id: uuid.UUID) -> DeviceConnection | None:
        key = self._sessions.get(session_id)
        return self._devices.get(key) if key else None

    async def connect(self, conn: DeviceConnection) -> None:
        """Register a device; it takes over the calls it left pending, if it comes back in time."""
        old = self._devices.get(conn.key)
        if old is not None and old is not conn:
            old.connected = False
            conn.inflight.update(old.inflight)
            old.inflight = {}
            with contextlib.suppress(Exception):
                await old.websocket.close(code=4409)  # replaced by a newer connection
        self._devices[conn.key] = conn
        if conn.session_id is not None:
            self._sessions[conn.session_id] = conn.key
        timer = self._orphan_timers.pop(conn.key, None)
        if timer is not None:
            timer.cancel()
        adopted = self._orphans.pop(conn.key, {})
        conn.inflight.update(adopted)
        for call in adopted.values():
            await self._request(conn, call)
        await self._announce_one(conn)

    async def disconnect(self, conn: DeviceConnection) -> None:
        """Forget a device; its pending calls wait out the grace period."""
        conn.connected = False
        if self._devices.get(conn.key) is not conn:
            return  # replaced already
        del self._devices[conn.key]
        if conn.session_id is not None and self._sessions.get(conn.session_id) == conn.key:
            del self._sessions[conn.session_id]
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.delete(f"faden:device:{_key_str(conn.key)}")
                if conn.session_id is not None:
                    await self._redis.delete(f"faden:device-session:{conn.session_id}")
        if conn.inflight:
            self._orphans[conn.key] = dict(conn.inflight)
            conn.inflight = {}
            self._orphan_timers[conn.key] = asyncio.create_task(self._expire_orphans(conn.key))

    async def _expire_orphans(self, key: DeviceKey) -> None:
        await asyncio.sleep(self.grace_sec)
        self._orphan_timers.pop(key, None)
        for call in self._orphans.pop(key, {}).values():
            gone = _failed(call.call_id, call.tool_id, "The device disconnected and did not come back.")
            await self._answer(call, gone)

    # -- calls ------------------------------------------------------------------------------

    async def call(
        self,
        *,
        tenant_id: str,
        user_id: str | None,
        session_id: uuid.UUID | None,
        tool_id: str,
        arguments: dict[str, Any],
        call_id: str,
        agent_id: str | None = None,
        approval: str | None = None,
        timeout: float = TOOL_CALL_TIMEOUT_SEC,
    ) -> ToolCallResult:
        """Run *tool_id* on the user's device and wait for its answer.

        With *approval*, the device asks its user first (``approval_required``
        in the request) and says ``tool_call_approval_pending`` to wait for them.

        Without a session bound to a device, any of the user's devices signed
        in for the same agent (*agent_id*; ``None``: signed in as the user)
        will do -- a device signed in for one agent never serves another's
        calls, nor a chat's.
        """
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        waiting = Waiting(call_id=call_id, tool_id=tool_id, future=loop.create_future(),
                          deadline=started + timeout, started=started)
        self._waiting[call_id] = waiting
        try:
            local = self._local_device(tenant_id, user_id, session_id, tool_id, agent_id)
            if local is not None:
                waiting.device = local.key
                call = InFlight(call_id=call_id, tool_id=tool_id, arguments=arguments, approval=approval)
                local.inflight[call_id] = call
                await self._request(local, call)
            else:
                target = await self._remote_device(tenant_id, user_id, session_id, agent_id)
                if target is None:
                    return _failed(call_id, tool_id, "No device is connected for this user.", started)
                device, worker = target
                waiting.worker, waiting.device = worker, device
                await self._publish(worker, {
                    "op": "call", "call_id": call_id, "tool_id": tool_id, "arguments": arguments,
                    "device": _key_str(device), "reply_to": self.worker_id, "approval": approval,
                })
            return await self._wait(waiting)
        finally:
            self._waiting.pop(call_id, None)

    async def _wait(self, waiting: Waiting) -> ToolCallResult:
        while True:
            remaining = waiting.deadline - time.monotonic()
            if remaining <= 0:
                await self._cancel(waiting)
                return _failed(
                    waiting.call_id, waiting.tool_id,
                    f"The device did not answer in time ({int(time.monotonic() - waiting.started)} s).",
                    waiting.started,
                )
            try:
                result = await asyncio.wait_for(asyncio.shield(waiting.future), timeout=remaining)
            except TimeoutError:
                continue  # the deadline may have moved; the loop decides
            return ToolCallResult(
                call_id=result.call_id, tool_id=result.tool_id, content=result.content,
                is_error=result.is_error, latency_ms=int((time.monotonic() - waiting.started) * 1000),
                realm=result.realm, executor=result.executor,
            )

    async def _cancel(self, waiting: Waiting) -> None:
        if waiting.worker is not None and waiting.worker != self.worker_id:
            with contextlib.suppress(Exception):
                await self._publish(waiting.worker, {"op": "cancel", "call_id": waiting.call_id,
                                                     "device": _key_str(waiting.device or ("", "", ""))})
            return
        await self._cancel_local(waiting.device, waiting.call_id)

    async def _cancel_local(self, device: DeviceKey | None, call_id: str) -> None:
        conn = self._devices.get(device) if device else None
        if conn is not None and conn.inflight.pop(call_id, None) is not None:
            with contextlib.suppress(Exception):
                await conn.send({"type": "tool_call_cancel", "call_id": call_id})
        if device is not None:
            self._orphans.get(device, {}).pop(call_id, None)

    def _local_device(
        self, tenant_id: str, user_id: str | None, session_id: uuid.UUID | None, tool_id: str,
        agent_id: str | None,
    ) -> DeviceConnection | None:
        if session_id is not None:
            bound = self.device_for_session(session_id)
            if bound is not None and bound.key[0] == tenant_id:
                return bound
        if user_id is None:
            return None
        mine = [
            c for k, c in self._devices.items()
            if k[0] == tenant_id and k[1] == user_id and c.connected and c.agent_id == agent_id
        ]
        return next((c for c in mine if c.offers(tool_id)), None)

    async def _remote_device(
        self, tenant_id: str, user_id: str | None, session_id: uuid.UUID | None, agent_id: str | None,
    ) -> tuple[DeviceKey, str] | None:
        if self._redis is None:
            return None
        try:
            if session_id is not None:
                bound = await self._redis.get(f"faden:device-session:{session_id}")
                if bound:
                    key = _key_of(_text(bound))
                    presence = await self._redis.get(f"faden:device:{_key_str(key)}")
                    if presence and key[0] == tenant_id:
                        return key, _presence(presence)[0]
            if user_id is None:
                return None
            pattern = f"faden:device:{tenant_id}:{user_id}:*"
            async for found in self._redis.scan_iter(match=pattern, count=100):
                name = _text(found)
                presence = await self._redis.get(name)
                if not presence:
                    continue
                worker, device_agent = _presence(presence)
                if device_agent == agent_id:
                    return _key_of(name[len("faden:device:"):]), worker
        except Exception:
            logger.warning("Device lookup in Redis failed", exc_info=True)
        return None

    async def _request(self, conn: DeviceConnection, call: InFlight) -> None:
        request = {
            "type": "tool_call_request",
            "call_id": call.call_id,
            "session_id": str(conn.session_id) if conn.session_id else None,
            "tool_id": call.tool_id,
            "arguments": call.arguments,
        }
        if call.approval:
            request["approval_required"] = {"reason": call.approval}
        await conn.send(request)

    # -- what devices say ---------------------------------------------------------------------

    async def on_result(self, conn: DeviceConnection, call_id: str, result: ToolCallResult) -> None:
        call = conn.inflight.pop(call_id, None)
        if call is None:
            logger.warning("Device %s answered a call it does not hold: %s", conn.key[2], call_id)
            return
        await self._answer(call, result)

    async def on_more_time(self, conn: DeviceConnection, call_id: str, seconds: float) -> bool:
        """The device needs *seconds* more for *call_id*; ``False`` when it holds no such call."""
        call = conn.inflight.get(call_id)
        if call is None:
            return False
        until = time.time() + seconds
        if call.reply_to is None:
            self._extend(call_id, until)
        else:
            await self._publish(call.reply_to, {"op": "extend", "call_id": call_id, "until": until})
        return True

    def _extend(self, call_id: str, until_wall: float) -> None:
        waiting = self._waiting.get(call_id)
        if waiting is not None:
            deadline = time.monotonic() + max(0.0, until_wall - time.time())
            waiting.deadline = max(waiting.deadline, deadline)

    async def _answer(self, call: InFlight, result: ToolCallResult) -> None:
        if call.reply_to is None:
            waiting = self._waiting.get(call.call_id)
            if waiting is not None and not waiting.future.done():
                waiting.future.set_result(result)
            return
        await self._publish(call.reply_to, {
            "op": "result", "call_id": call.call_id, "tool_id": result.tool_id,
            "content": result.content, "is_error": result.is_error,
        })

    # -- between workers -----------------------------------------------------------------------

    async def _publish(self, worker: str, message: dict[str, Any]) -> None:
        if self._redis is None:
            raise RuntimeError("No Redis: devices on other workers cannot be reached.")
        await self._redis.publish(f"faden:worker:{worker}", json.dumps(message))

    async def _listen(self, ready: asyncio.Event) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(f"faden:worker:{self.worker_id}")
        ready.set()
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None or message.get("type") != "message":
                    continue
                try:
                    await self._on_worker_message(json.loads(message["data"]))
                except Exception:
                    logger.warning("A message from another worker failed", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()

    async def _on_worker_message(self, msg: dict[str, Any]) -> None:
        op = msg.get("op")
        call_id = str(msg.get("call_id", ""))
        if op == "call":
            key = _key_of(msg["device"])
            call = InFlight(call_id=call_id, tool_id=msg["tool_id"], arguments=msg.get("arguments") or {},
                            reply_to=msg["reply_to"], approval=msg.get("approval"))
            conn = self._devices.get(key)
            if conn is not None:
                conn.inflight[call_id] = call
                await self._request(conn, call)
            elif key in self._orphans:
                self._orphans[key][call_id] = call  # sent when it reconnects
            else:
                await self._answer(call, _failed(call_id, call.tool_id, "The device is no longer connected."))
        elif op == "result":
            waiting = self._waiting.get(call_id)
            if waiting is not None and not waiting.future.done():
                waiting.future.set_result(ToolCallResult(
                    call_id=call_id, tool_id=msg.get("tool_id", waiting.tool_id),
                    content=msg.get("content", ""), is_error=bool(msg.get("is_error")),
                    realm="client_local", executor="ClientToolBroker",
                ))
        elif op == "extend":
            self._extend(call_id, float(msg.get("until", 0)))
        elif op == "cancel":
            await self._cancel_local(_key_of(msg["device"]) if msg.get("device") else None, call_id)

    async def _announce(self) -> None:
        while True:
            await asyncio.sleep(PRESENCE_TTL_SEC / 3)
            for conn in list(self._devices.values()):
                await self._announce_one(conn)

    async def _announce_one(self, conn: DeviceConnection) -> None:
        if self._redis is None:
            return
        try:
            presence = json.dumps({"worker": self.worker_id, "agent": conn.agent_id})
            await self._redis.set(f"faden:device:{_key_str(conn.key)}", presence, ex=PRESENCE_TTL_SEC)
            if conn.session_id is not None:
                await self._redis.set(
                    f"faden:device-session:{conn.session_id}", _key_str(conn.key), ex=PRESENCE_TTL_SEC,
                )
        except Exception:
            logger.warning("Could not announce device %s", conn.key[2], exc_info=True)


#: This worker's hub; the lifespan starts it with Redis when there is one.
device_hub = DeviceHub()


# ---------------------------------------------------------------------------
# The WebSocket
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceIdentity:
    """Who a connection authenticated as."""

    tenant_id: str
    user_id: str
    device_id: str | None  # from an agent token's ``did``
    agent_id: str | None


#: ``(token, message) -> identity``; raises ``PermissionError`` with a code when refused.
Authenticator = Any


def _token_from_handshake(ws: Any) -> str | None:
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    for proto in ws.scope.get("subprotocols") or []:
        if proto.startswith(BEARER_PREFIX):
            return proto[len(BEARER_PREFIX):] or None
    return None


def _tool_records(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tool_id": t.get("tool_id", ""),
            "realm": t.get("realm", "client_local"),
            "schema": t.get("schema"),
            "available": t.get("available", True),
        }
        for t in tools
        if isinstance(t, dict)
    ]


async def _error(ws: Any, code: str, message: str) -> None:
    await ws.send_json({"type": "error", "code": code, "message": message})


#: How often a connected device's caller is checked again. The token was checked once, at connect: a revoked
#: or expired token, a suspended person, an agent whose access was taken away went on getting tool calls for
#: as long as the device stayed connected -- hours.
RECHECK_SEC = 60.0

#: Refusals that mean "this token is no good" (4401); the others say the caller may not (4403).
_UNAUTHENTICATED = frozenset({
    "invalid_token", "invalid_token_sub", "missing_authorization", "sign_in_outdated",
    "api_token_revoked", "api_token_expired", "api_token_outdated",
})
#: Refusals that say nothing about the caller -- the check itself could not be made -- try again next round.
_TRANSIENT = frozenset({"access_check_failed", "agent_lookup_failed", "jwt_not_configured"})


@dataclass
class _Caller:
    """The token a connection runs on; ``token_refresh`` replaces it."""

    token: str | None


def _same_caller(identity: DeviceIdentity, conn: DeviceConnection) -> bool:
    tenant, user, device = conn.key
    return (
        identity.tenant_id == tenant
        and identity.user_id == user
        and identity.agent_id == conn.agent_id
        and (identity.device_id is None or identity.device_id == device)
    )


async def _refuse(ws: Any, code: str, message: str) -> None:
    with contextlib.suppress(Exception):
        await _error(ws, code, message)
    with contextlib.suppress(Exception):
        await ws.close(code=4401 if code in _UNAUTHENTICATED else 4403)


async def _still_allowed(ws: Any, caller: _Caller, conn: DeviceConnection, authenticate: Authenticator) -> bool:
    """Whether the connection's caller may still be served; refuses and closes it when not."""
    if caller.token is None:
        return True
    try:
        identity: DeviceIdentity = await authenticate(caller.token)
    except PermissionError as exc:
        code = str(exc.args[0])
        if code in _TRANSIENT:
            return True
        await _refuse(ws, code, str(exc.args[1] if len(exc.args) > 1 else "Refused."))
        return False
    if not _same_caller(identity, conn):
        await _refuse(ws, "token_mismatch", "The token is someone else's.")
        return False
    return True


async def _recheck(ws: Any, caller: _Caller, conn: DeviceConnection, authenticate: Authenticator) -> None:
    while conn.connected:
        await asyncio.sleep(RECHECK_SEC)
        if not await _still_allowed(ws, caller, conn, authenticate):
            return


async def _refresh_token(
    ws: Any, msg: dict[str, Any], caller: _Caller, conn: DeviceConnection, authenticate: Authenticator,
) -> bool:
    """``token_refresh``: carry on with a new token of the same caller. False when it was refused."""
    token = msg.get("token")
    if not isinstance(token, str) or not token:
        await _error(ws, "missing_authorization", "token_refresh needs a token.")
        return True
    renewed = _Caller(token)
    if not await _still_allowed(ws, renewed, conn, authenticate):
        return False
    caller.token = token
    await ws.send_json({"type": "token_refreshed"})
    return True


async def handle_client_websocket(
    ws: Any,
    dao: GatewayDAO,
    *,
    authenticate: Authenticator,
    owns_session: Any,
    hub: DeviceHub | None = None,
) -> None:
    """A device's connection, from authentication to goodbye.

    *authenticate* ``(token) -> DeviceIdentity`` verifies a token (raising
    ``PermissionError(code, message)``); *owns_session* ``(identity, session_id)
    -> bool`` tells whether a session is the user's.
    """
    hub = hub or device_hub
    handshake_token = _token_from_handshake(ws)
    identity: DeviceIdentity | None = None
    if handshake_token is not None:
        try:
            identity = await authenticate(handshake_token)
        except PermissionError as exc:
            await ws.close(code=4401 if exc.args[0] in ("invalid_token", "missing_authorization") else 4403)
            return
    offered = ws.scope.get("subprotocols") or []
    await ws.accept(subprotocol=SUBPROTOCOL if SUBPROTOCOL in offered else None)

    conn: DeviceConnection | None = None
    watch: asyncio.Task[None] | None = None
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SEC)
        conn = await _init(ws, first, dao, identity, authenticate, owns_session, hub)
        if conn is None:
            await ws.close(code=4401)
            return
        caller = _Caller(handshake_token or _init_token(first))
        watch = asyncio.create_task(_recheck(ws, caller, conn, authenticate))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _error(ws, "invalid_json", "Expected JSON.")
                continue
            if isinstance(msg, dict) and msg.get("type") == "token_refresh":
                if not await _refresh_token(ws, msg, caller, conn, authenticate):
                    return
                continue
            await _dispatch(conn, msg, dao, hub)
    except TimeoutError:
        with contextlib.suppress(Exception):
            await ws.close(code=4401)
    except Exception:
        logger.debug("Device channel closed", exc_info=True)
    finally:
        if watch is not None:
            watch.cancel()
        if conn is not None:
            await hub.disconnect(conn)
            if conn.session_id is not None:
                with contextlib.suppress(Exception):
                    await dao.mark_client_tools_unavailable(conn.session_id, conn.key[2])
            logger.info("Device %s of user %s disconnected", conn.key[2], conn.key[1])


def _init_token(raw: str) -> str | None:
    """The token ``session_init`` carried, when the handshake had none."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None
    token = msg.get("token") if isinstance(msg, dict) else None
    return token if isinstance(token, str) and token else None


async def _init(
    ws: Any,
    raw: str,
    dao: GatewayDAO,
    identity: DeviceIdentity | None,
    authenticate: Authenticator,
    owns_session: Any,
    hub: DeviceHub,
) -> DeviceConnection | None:
    """``session_init``: who the device is, what it offers, which session it serves."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        msg = {}
    if not isinstance(msg, dict) or msg.get("type") != "session_init":
        await _error(ws, "not_initialised", "Send session_init first.")
        return None
    identity = identity or await _authenticate_init(ws, msg, authenticate)
    if identity is None:
        return None
    device_id = identity.device_id or str(msg.get("client_id") or "").strip()
    if not device_id or len(device_id) > 128:
        await _error(ws, "invalid_session_init", "A client_id (1-128 characters) is required.")
        return None
    session_id: uuid.UUID | None = None
    if msg.get("session_id"):
        try:
            session_id = uuid.UUID(str(msg["session_id"]))
        except ValueError:
            await _error(ws, "invalid_session_id", "Invalid session_id format.")
            return None
        if not await owns_session(identity, session_id):
            await _error(ws, "session_not_found", "No such session of yours.")
            return None
    tools = [t for t in msg.get("tools") or [] if isinstance(t, dict)]
    conn = DeviceConnection(
        key=(identity.tenant_id, identity.user_id, device_id),
        websocket=ws, tools=tools, session_id=session_id, agent_id=identity.agent_id,
    )
    if session_id is not None:
        await dao.register_client_capabilities(session_id, device_id, _tool_records(tools))
    await ws.send_json({
        "type": "session_ack",
        "session_id": str(session_id) if session_id else None,
        "client_id": device_id,
        "device_id": device_id,
        "tools_registered": len(tools),
    })
    await hub.connect(conn)
    logger.info("Device %s of user %s connected with %d tools", device_id, identity.user_id, len(tools))
    return conn


async def _authenticate_init(
    ws: Any, msg: dict[str, Any], authenticate: Authenticator,
) -> DeviceIdentity | None:
    """The identity of the token in ``session_init``; ``None`` when refused (the device is told why)."""
    token = msg.get("token")
    if not isinstance(token, str) or not token:
        await _error(ws, "missing_authorization", "Send a token: in the header, subprotocol or session_init.")
        return None
    try:
        identity: DeviceIdentity = await authenticate(token)
    except PermissionError as exc:
        await _error(ws, str(exc.args[0]), str(exc.args[1] if len(exc.args) > 1 else "Refused."))
        return None
    return identity


async def _dispatch(conn: DeviceConnection, msg: dict[str, Any], dao: GatewayDAO, hub: DeviceHub) -> None:
    kind = msg.get("type", "")
    call_id = str(msg.get("call_id", ""))
    if kind == "tool_call_result":
        result = msg.get("result")
        content = result.get("content") if isinstance(result, dict) else result
        await hub.on_result(conn, call_id, ToolCallResult(
            call_id=call_id, tool_id=str(msg.get("tool_id", "")),
            content=content if isinstance(content, str) else json.dumps(content),
            is_error=msg.get("status", "ok") != "ok", realm="client_local", executor="ClientToolBroker",
        ))
    elif kind == "tool_call_error":
        error = str(msg.get("error") or "The tool failed on the device.")
        await hub.on_result(conn, call_id, _failed(call_id, str(msg.get("tool_id", "")), error))
    elif kind in ("tool_call_progress", "tool_call_approval_pending"):
        asked = msg.get("timeout_s")
        if kind == "tool_call_progress":
            seconds = TOOL_CALL_TIMEOUT_SEC
        elif isinstance(asked, int | float):
            seconds = min(float(asked), APPROVAL_TIMEOUT_SEC)
        else:
            seconds = APPROVAL_TIMEOUT_SEC
        if not await hub.on_more_time(conn, call_id, seconds):
            await _error(conn.websocket, "unknown_call", f"No pending call {call_id}.")
    elif kind == "tool_registry_update":
        conn.tools = [t for t in msg.get("tools") or [] if isinstance(t, dict)]
        if conn.session_id is not None:
            await dao.register_client_capabilities(conn.session_id, conn.key[2], _tool_records(conn.tools))
        await conn.send({"type": "tool_registry_update_ack", "tools_registered": len(conn.tools)})
    elif kind == "ping":
        await conn.send({"type": "pong"})
    else:
        await _error(conn.websocket, "unknown_message_type", f"Unknown message type: {kind}")


# ---------------------------------------------------------------------------
# The broker's entry point
# ---------------------------------------------------------------------------


async def broker_tool_call(
    *,
    session_id: uuid.UUID,
    tool_id: str,
    arguments: dict[str, Any],
    call_id: str,
    tenant_id: str = "default",
    user_id: str | None = None,
    agent_id: str | None = None,
    approval: str | None = None,
    timeout: float = TOOL_CALL_TIMEOUT_SEC,
    hub: DeviceHub | None = None,
) -> ToolCallResult:
    """Run *tool_id* on the device serving *session_id* (or another of the user's devices)."""
    return await (hub or device_hub).call(
        tenant_id=tenant_id, user_id=user_id, session_id=session_id,
        tool_id=tool_id, arguments=arguments, call_id=call_id, agent_id=agent_id,
        approval=approval, timeout=timeout,
    )
