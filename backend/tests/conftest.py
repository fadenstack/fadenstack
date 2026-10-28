import os
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from aio_pika import Channel
from aio_pika.abc import AbstractExchange, AbstractQueue
from aio_pika.pool import Pool
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.utils import create_database, drop_database
from faden_backend.services.rabbit.dependencies import get_rmq_channel_pool
from faden_backend.services.rabbit.lifespan import init_rabbit, shutdown_rabbit
from faden_backend.settings import settings
from faden_backend.web.application import get_app


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_gateway_erase(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The gateway's erase (an administrator's delete, an account deleted) is answered here, never sent.

    Returns the calls, so a test can check what was asked.
    """
    from faden_backend.services.chat.gateway_client import GatewayChatClient  # noqa: PLC0415

    calls: list[tuple[str, str]] = []

    async def erase(self: Any, scope: str, target: str, *, secret: str) -> dict[str, int]:
        calls.append((scope, target))
        return {"chats": 1, "projects": 1, "files": 1, "memory_facts": 0}

    monkeypatch.setattr(GatewayChatClient, "erase", erase)
    return calls


class RecordingMailSender:
    """A mail sender that is on and keeps what it was given (core has none; Enterprise registers its mailer)."""

    def __init__(self) -> None:
        self.on = True
        self.sent: list[tuple[str, dict[str, Any]]] = []
        #: The key each send was given, in the order of ``sent``.
        self.keys: list[str] = []
        self.base = "https://console.example.com"
        self.recipients: list[str] = ["ops@example.com"]

    def available(self) -> bool:
        """Whether mail goes out: as the test sets ``on``."""
        return self.on

    async def send(self, kind: str, payload: dict[str, Any], *, key: str) -> None:
        """Keeps the message and its key."""
        self.sent.append((kind, payload))
        self.keys.append(key)

    def link_base(self) -> str:
        """The address links start with, as the test sets ``base``."""
        return self.base

    def admin_recipients(self) -> list[str]:
        """Who gets alerts, as the test sets ``recipients``."""
        return self.recipients


@pytest.fixture
def mail_on(monkeypatch: pytest.MonkeyPatch) -> RecordingMailSender:
    """Mail can be sent, as with Enterprise's mailer switched on."""
    from faden_backend.services.extensions import registry  # noqa: PLC0415

    sender = RecordingMailSender()
    monkeypatch.setattr(registry, "mail_sender", sender)
    return sender


@pytest.fixture(scope="session", autouse=True)
def _run_from_the_repository_root() -> Generator[None]:
    """
    Run the tests with the repository root as the working directory.

    The settings' default file paths (``shared/grafana/...``, ``shared/prometheus/...``)
    are relative to the repository root, where the backend is run from. Started from
    ``backend/`` the tests that render the Grafana dashboards could not find their template.

    :yield: nothing; the previous directory is restored afterwards.
    """
    previous = Path.cwd()
    os.chdir(REPO_ROOT)
    try:
        yield
    finally:
        os.chdir(previous)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """
    Backend for anyio pytest plugin.

    :return: backend name.
    """
    return "asyncio"


@pytest.fixture(scope="session")
async def _engine(anyio_backend: Any) -> AsyncGenerator[AsyncEngine]:
    """
    Create engine and databases.

    :yield: new engine.
    """
    from faden_backend.db.meta import meta
    from faden_backend.db.models import load_all_models

    load_all_models()

    try:
        await create_database()
    except Exception:
        pass

    engine = create_async_engine(str(settings.db_url))
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
        # What the migration adds beside the models: the count of changes to access, and its triggers.
        from sqlalchemy import text

        from faden_backend.db import access_epoch

        for statement in access_epoch.create_all():
            await conn.execute(text(statement))

    try:
        yield engine
    finally:
        await engine.dispose()
        try:
            await drop_database()
        except Exception:
            pass


@pytest.fixture
async def dbsession(
    _engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession]:
    """
    Get session to database.

    Fixture that returns a SQLAlchemy session with a SAVEPOINT, and the rollback to it
    after the test completes.

    :param _engine: current engine.
    :yields: async session.
    """
    connection = await _engine.connect()
    trans = await connection.begin()

    session_maker = async_sessionmaker(
        connection,
        expire_on_commit=False,
    )
    session = session_maker()

    try:
        yield session
    finally:
        await session.close()
        try:
            if trans.is_active:
                await trans.rollback()
        except Exception:
            pass
        await connection.close()


@pytest.fixture
async def test_rmq_pool() -> AsyncGenerator[Channel]:
    """
    Create rabbitMQ pool.

    :yield: channel pool.
    """
    import asyncio

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        try:
            asyncio.set_event_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass

    app_mock = Mock()
    try:
        init_rabbit(app_mock)
        yield app_mock.state.rmq_channel_pool
        await shutdown_rabbit(app_mock)
    except Exception:
        mock_pool = Mock()
        yield mock_pool


@pytest.fixture
async def test_exchange_name() -> str:
    """
    Name of an exchange to use in tests.

    :return: name of an exchange.
    """
    return uuid.uuid4().hex


@pytest.fixture
async def test_routing_key() -> str:
    """
    Name of routing key to use while binding test queue.

    :return: key string.
    """
    return uuid.uuid4().hex


@pytest.fixture
async def test_exchange(
    test_exchange_name: str,
    test_rmq_pool: Pool[Channel],
) -> AsyncGenerator[AbstractExchange]:
    """
    Creates test exchange.

    :param test_exchange_name: name of an exchange to create.
    :param test_rmq_pool: channel pool for rabbitmq.
    :yield: created exchange.
    """
    async with test_rmq_pool.acquire() as conn:
        exchange = await conn.declare_exchange(
            name=test_exchange_name,
            auto_delete=True,
        )
        yield exchange

        await exchange.delete(if_unused=False)


@pytest.fixture
async def test_queue(
    test_exchange: AbstractExchange,
    test_rmq_pool: Pool[Channel],
    test_routing_key: str,
) -> AsyncGenerator[AbstractQueue]:
    """
    Creates queue connected to exchange.

    :param test_exchange: exchange to bind queue to.
    :param test_rmq_pool: channel pool for rabbitmq.
    :param test_routing_key: routing key to use while binding.
    :yield: queue binded to test exchange.
    """
    async with test_rmq_pool.acquire() as conn:
        queue = await conn.declare_queue(name=uuid.uuid4().hex)
        await queue.bind(
            exchange=test_exchange,
            routing_key=test_routing_key,
        )
        yield queue

        await queue.delete(if_unused=False, if_empty=False)


@pytest.fixture
def fastapi_app(
    dbsession: AsyncSession,
    test_rmq_pool: Pool[Channel],
) -> FastAPI:
    """
    Fixture for creating FastAPI app.

    :return: fastapi app with mocked dependencies.
    """
    application = get_app()
    application.dependency_overrides[get_db_session] = lambda: dbsession
    application.dependency_overrides[get_rmq_channel_pool] = lambda: test_rmq_pool
    return application


@pytest.fixture
async def client(fastapi_app: FastAPI, anyio_backend: Any) -> AsyncGenerator[AsyncClient]:
    """
    Fixture that creates client for requesting server.

    :param fastapi_app: the application.
    :yield: client for the app.
    """
    async with AsyncClient(
        transport=ASGITransport(fastapi_app),
        base_url="http://test",
        timeout=2.0,
        follow_redirects=True,
    ) as ac:
        yield ac
