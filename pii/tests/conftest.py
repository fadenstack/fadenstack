from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from faden_pii.settings import settings
from faden_pii.web.application import get_app

# No database: the service opens none (its DB layer is unused template code,
# and no install gives it credentials). The fixtures created one, so every
# API test failed on a login the service never needs.


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """
    Backend for anyio pytest plugin.

    :return: backend name.
    """
    return "asyncio"


TEST_TOKEN = "test-pii-token"  # noqa: S105


@pytest.fixture(autouse=True)
def _service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service refuses calls without its token; tests present this one."""
    monkeypatch.setattr(settings, "service_token", TEST_TOKEN)


@pytest.fixture
def fastapi_app() -> FastAPI:
    """
    Fixture for creating FastAPI app.

    :return: fastapi app.
    """
    return get_app()


@pytest.fixture
async def client(
    fastapi_app: FastAPI,
    anyio_backend: Any,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Fixture that creates client for requesting server.

    :param fastapi_app: the application.
    :yield: client for the app.
    """
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        timeout=2.0,
        headers={"authorization": f"Bearer {TEST_TOKEN}"},
    ) as ac:
        yield ac
