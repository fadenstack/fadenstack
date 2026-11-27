"""A stream that outlives its reaped session: the machine must not stay offline.

The node stream handler holds its ``InfraNode`` and its ``InfraNodeSession``
for the whole connection, with ``expire_on_commit`` off. The stale-session
reaper runs in another database session. On a Mac that slept with TCP
keepalive on, the socket survived, heartbeats stopped for longer than the stale
window, and the reaper closed the session and marked the machine offline.
When the Mac woke, the same stream carried on heartbeating -- and the machine
stayed offline: the handler's copy of the node still read "healthy", so
assigning "healthy" was no change to the ORM and only ``last_seen`` was written.

The reaper's writes are reproduced here as a core UPDATE that does not touch
the identity map (``synchronize_session=False``): what another session's
commit looks like to the stream's long-lived objects.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.node_control_dao import NodeControlDAO
from faden_backend.db.models.node_control import InfraNode, InfraNodeSession, NodeHealthStatus
from faden_backend.services.nodes.service import NodeControlService
from faden_backend.web.api.admin.system.views import STREAM_SESSION_REAPED

pytestmark = pytest.mark.anyio()


def _service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(
        NodeControlDAO(session),
        pepper="test-pepper",
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=60,
    )


async def _connected(session: AsyncSession) -> tuple[InfraNode, InfraNodeSession]:
    """A healthy machine with one open stream session, as the handler holds them."""
    dao = NodeControlDAO(session)
    node = InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host="192.0.2.80",
        status=NodeHealthStatus.HEALTHY.value,
        capabilities_json={},
    )
    session.add(node)
    await session.flush()
    credential_id = uuid.uuid4()
    await dao.create_credential(node_id=node.id, credential_id=credential_id, secret_hash="x" * 32)
    stream_session = await dao.create_session(node_id=node.id, credential_id=credential_id)
    await session.flush()
    return node, stream_session


async def _reaped_elsewhere(session: AsyncSession, node: InfraNode, stream_session: InfraNodeSession) -> None:
    """What the reaper commits from its own session: session closed, machine offline."""
    await session.execute(
        update(InfraNodeSession)
        .where(InfraNodeSession.id == stream_session.id)
        .values(disconnected_at=datetime.now(tz=UTC))
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(InfraNode)
        .where(InfraNode.id == node.id)
        .values(status=NodeHealthStatus.OFFLINE.value)
        .execution_options(synchronize_session=False)
    )


async def _status_in_db(session: AsyncSession, node_id: uuid.UUID) -> str:
    return (await session.execute(select(InfraNode.status).where(InfraNode.id == node_id))).scalar_one()


async def test_the_reaper_change_is_invisible_to_the_streams_copy(dbsession: AsyncSession) -> None:
    """The premise: the handler's objects do not see the reaper's writes on their own."""
    node, stream_session = await _connected(dbsession)
    await _reaped_elsewhere(dbsession, node, stream_session)
    assert node.status == NodeHealthStatus.HEALTHY.value
    assert stream_session.disconnected_at is None
    assert await _status_in_db(dbsession, node.id) == NodeHealthStatus.OFFLINE.value


async def test_a_heartbeat_after_the_reaper_makes_the_machine_healthy_again(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    node, stream_session = await _connected(dbsession)
    await _reaped_elsewhere(dbsession, node, stream_session)

    await service.heartbeat_node(node=node, status="healthy", capabilities={}, version="0.2.12", host="192.0.2.80")
    await dbsession.flush()

    assert await _status_in_db(dbsession, node.id) == NodeHealthStatus.HEALTHY.value


async def test_the_stream_learns_its_session_was_reaped(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    node, stream_session = await _connected(dbsession)
    assert await service.stream_session_was_closed(session=stream_session) is False

    await _reaped_elsewhere(dbsession, node, stream_session)

    assert await service.stream_session_was_closed(session=stream_session) is True


async def test_a_normal_session_is_left_open_and_its_heartbeat_unchanged(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    node, stream_session = await _connected(dbsession)

    for _ in range(3):
        assert await service.stream_session_was_closed(session=stream_session) is False
        await service.heartbeat_node(node=node, status="healthy", capabilities={"os": "linux"}, version="0.2.12")
        await dbsession.flush()

    assert await _status_in_db(dbsession, node.id) == NodeHealthStatus.HEALTHY.value
    assert node.capabilities_json == {"os": "linux"}
    await dbsession.refresh(stream_session)
    assert stream_session.disconnected_at is None


async def test_maintenance_set_elsewhere_is_kept_by_the_next_heartbeat(dbsession: AsyncSession) -> None:
    """The same stale copy used to miss an operator's maintenance or drain set from the console."""
    service = _service(dbsession)
    node, _stream_session = await _connected(dbsession)
    await dbsession.execute(
        update(InfraNode)
        .where(InfraNode.id == node.id)
        .values(maintenance_mode=True, status="maintenance")
        .execution_options(synchronize_session=False)
    )

    await service.heartbeat_node(node=node, status="healthy", capabilities={}, version="0.2.12")
    await dbsession.flush()

    assert await _status_in_db(dbsession, node.id) == "maintenance"


def test_the_reaped_close_code_is_its_own() -> None:
    # 4401 unauthorised and 4408 idle timeout are taken; any close makes an agent reconnect.
    assert STREAM_SESSION_REAPED == 4410


# ── the stream handler itself ─────────────────────────────────────────────


class _Session:
    """The test's database session as the handler's own: commit flushes, close does nothing."""

    def __init__(self, inner: AsyncSession) -> None:
        self._inner = inner
        self.expire_on_commit = False

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self._inner, name)

    async def commit(self) -> None:
        await self._inner.flush()

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _Socket:
    """A websocket that hands the handler *frames*, then disconnects."""

    def __init__(self, session: AsyncSession, frames: list[dict], before_first=None) -> None:  # noqa: ANN001
        from types import SimpleNamespace

        self.app = SimpleNamespace(state=SimpleNamespace(db_session_factory=lambda: _Session(session)))
        self.headers = {"authorization": "Bearer test"}
        self.client = None
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self._frames = list(frames)
        self._before_first = before_first

    async def accept(self) -> None:
        return None

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_json(self) -> dict:
        from fastapi import WebSocketDisconnect

        if self._before_first is not None:
            hook, self._before_first = self._before_first, None
            await hook()
        if not self._frames:
            raise WebSocketDisconnect(code=1000)
        return self._frames.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


async def _run_stream(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch, frames: list[dict], *, reap_first: bool
) -> tuple[InfraNode, _Socket, list[InfraNodeSession]]:
    from faden_backend.web.api.admin.system.views import system_node_stream

    node, _ = await _connected(dbsession)  # the machine; the handler opens its own session row
    from faden_backend.db.models.node_control import InfraNodeCredential

    credential = (
        await dbsession.execute(select(InfraNodeCredential).where(InfraNodeCredential.node_id == node.id))
    ).scalars().first()

    async def authenticate(self, *, authorization):  # noqa: ANN001, ANN202
        return node, credential

    monkeypatch.setattr(NodeControlService, "authenticate_agent", authenticate)

    async def reap() -> None:
        # Every open session of the machine, as the reaper closes them after the sleep.
        await dbsession.execute(
            update(InfraNodeSession)
            .where(InfraNodeSession.node_id == node.id, InfraNodeSession.disconnected_at.is_(None))
            .values(disconnected_at=datetime.now(tz=UTC))
            .execution_options(synchronize_session=False)
        )
        await dbsession.execute(
            update(InfraNode)
            .where(InfraNode.id == node.id)
            .values(status=NodeHealthStatus.OFFLINE.value)
            .execution_options(synchronize_session=False)
        )

    socket = _Socket(dbsession, frames, before_first=reap if reap_first else None)
    await system_node_stream(socket)  # type: ignore[arg-type]
    rows = list(
        (await dbsession.execute(select(InfraNodeSession).where(InfraNodeSession.node_id == node.id))).scalars()
    )
    for row in rows:
        await dbsession.refresh(row)
    return node, socket, rows


HEARTBEAT = {"type": "heartbeat", "seq": 1, "status": "healthy", "version": "0.2.12", "capabilities": {}}


async def test_a_stream_whose_session_was_reaped_is_closed_to_reconnect(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    node, socket, rows = await _run_stream(dbsession, monkeypatch, [HEARTBEAT], reap_first=True)

    assert socket.sent and socket.sent[0]["type"] == "hello_ack"
    assert socket.closed is not None and socket.closed[0] == STREAM_SESSION_REAPED
    # The reaper's close stands (its time is not overwritten), and the heartbeat
    # on the dead session was not applied: the agent's reconnect brings it back.
    handler_sessions = [row for row in rows if row.credential_id is not None]
    assert all(row.disconnected_at is not None for row in handler_sessions)
    assert await _status_in_db(dbsession, node.id) == NodeHealthStatus.OFFLINE.value


async def test_a_normal_stream_takes_its_heartbeat_and_is_not_closed_by_the_server(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    node, socket, _rows = await _run_stream(dbsession, monkeypatch, [HEARTBEAT], reap_first=False)

    assert socket.sent and socket.sent[0]["type"] == "hello_ack"
    assert socket.closed is None  # the agent left (disconnect); the server closed nothing
    await dbsession.refresh(node)
    assert node.last_seen is not None
