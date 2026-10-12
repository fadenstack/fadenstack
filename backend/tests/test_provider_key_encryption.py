"""Provider API keys are stored sealed, in the backend DB and in the gateway's."""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.llm_dao import ProviderDAO
from faden_backend.db.models.llm import LLMProvider, ProviderTarget, ProviderType
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.llm.gateway_sync import GatewaySyncService
from faden_backend.services.llm.provider_keys import ProviderKeyError, open_provider_key, seal_provider_key
from faden_backend.services.llm.service import LLMService
from faden_backend.services.system_settings.crypto import SettingsCrypto
from faden_backend.settings import settings
from faden_backend.web.api.llm.dependencies import get_llm_service

KEY = "sk-live-0123456789abcdef"


@pytest.fixture(autouse=True)
def llm_service(fastapi_app: FastAPI) -> LLMService:
    service = LLMService(SimpleNamespace(), gateway_sync=GatewaySyncService(None))  # type: ignore[arg-type]
    fastapi_app.dependency_overrides[get_llm_service] = lambda: service
    return service


@pytest.fixture
async def superuser(dbsession: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(), email="keys@example.com", hashed_password="x",
        is_active=True, is_verified=True, is_superuser=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


async def _stored_key(dbsession: AsyncSession, provider_id: uuid.UUID) -> str | None:
    row = await dbsession.execute(
        sa.text("SELECT api_key_encrypted FROM llm_providers WHERE id = :id"), {"id": provider_id},
    )
    return row.scalar_one()


def test_seal_and_open_round_trip() -> None:
    sealed = seal_provider_key(KEY)
    assert sealed and KEY not in sealed and SettingsCrypto.is_ciphertext(sealed)
    assert SettingsCrypto(settings.settings_master_key).decrypt(sealed) == KEY
    assert open_provider_key(sealed) == KEY


def test_empty_key_is_no_key() -> None:
    assert seal_provider_key(None) is None
    assert seal_provider_key("") is None
    assert open_provider_key(None) is None


def test_sealing_ciphertext_again_is_refused() -> None:
    with pytest.raises(ProviderKeyError):
        seal_provider_key(seal_provider_key(KEY))


def test_opening_plaintext_fails_loudly() -> None:
    with pytest.raises(ProviderKeyError):
        open_provider_key(KEY)


def test_no_master_key_means_no_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "settings_master_key", "")
    with pytest.raises(ProviderKeyError):
        seal_provider_key(KEY)


async def test_create_stores_the_key_sealed(dbsession: AsyncSession, llm_service: LLMService) -> None:
    provider = await llm_service.create_provider(
        ProviderDAO(session=dbsession),
        name="openrouter",
        type_=ProviderType.CLOUD,
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url="https://openrouter.ai/api",
        api_key=KEY,
    )
    await dbsession.flush()
    stored = await _stored_key(dbsession, provider.id)
    assert stored and stored != KEY and KEY not in stored
    assert open_provider_key(stored) == KEY


async def test_create_without_key_stores_none(dbsession: AsyncSession, llm_service: LLMService) -> None:
    provider = await llm_service.create_provider(
        ProviderDAO(session=dbsession), name="local", type_=ProviderType.VLLM,
        endpoint_url="http://vllm:8000", api_key="",
    )
    await dbsession.flush()
    assert await _stored_key(dbsession, provider.id) is None


async def test_update_seals_and_the_api_never_returns_the_key(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, superuser: User,
) -> None:
    provider = await ProviderDAO(session=dbsession).create(
        name="p", type_=ProviderType.VLLM, target=ProviderTarget.LOCAL_DOCKER,
        capabilities={}, endpoint_url="http://vllm:8000/v1", extra_params={},
    )
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser

    resp = await client.patch(f"/api/llm/providers/{provider.id}", json={"api_key": KEY})
    assert resp.status_code == 200
    assert KEY not in resp.text and "api_key" not in resp.json()

    stored = await _stored_key(dbsession, provider.id)
    assert stored and stored != KEY
    assert open_provider_key(stored) == KEY

    for url in ("/api/llm/providers", f"/api/llm/providers/{provider.id}"):
        got = await client.get(url)
        assert got.status_code == 200
        assert KEY not in got.text and "api_key" not in got.text

    # An empty key clears it.
    cleared = await client.patch(f"/api/llm/providers/{provider.id}", json={"api_key": ""})
    assert cleared.status_code == 200
    assert await _stored_key(dbsession, provider.id) is None


class _RecordingSession:
    def __init__(self) -> None:
        self.params: list[dict] = []

    async def execute(self, stmt, params=None):
        self.params.append(dict(params or {}))
        result = MagicMock()
        result.fetchall.return_value = []
        result.scalar.return_value = None
        return result

    async def commit(self) -> None: ...
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return None


async def test_sync_writes_ciphertext_the_gateway_can_open() -> None:
    session = _RecordingSession()
    sync = GatewaySyncService(MagicMock(return_value=session))
    sealed = seal_provider_key(KEY)
    await sync.publish_runtime(
        runtime_id=uuid.uuid4(), alias="m", base_url="https://api.example.com",
        backend_provider_type="vllm", is_remote=True, api_key_encrypted=sealed,
    )
    written = [p["api_key"] for p in session.params if "api_key" in p]
    assert written == [sealed]
    assert KEY not in written[0]
    # The gateway derives the same Fernet key from the same master key (sha256, no purpose).
    assert SettingsCrypto(settings.settings_master_key).decrypt(written[0]) == KEY


@pytest.mark.parametrize("method", ["publish_runtime", "publish_inference_endpoint"])
async def test_sync_refuses_plaintext(method: str) -> None:
    session = _RecordingSession()
    sync = GatewaySyncService(MagicMock(return_value=session))
    kwargs = (
        dict(runtime_id=uuid.uuid4(), alias="m", base_url="https://x", backend_provider_type="vllm", is_remote=True)
        if method == "publish_runtime"
        else dict(deployment_id=uuid.uuid4(), base_url="https://x")
    )
    with pytest.raises(ValueError):
        await getattr(sync, method)(api_key_encrypted=KEY, **kwargs)
    assert session.params == []


# -- migration ---------------------------------------------------------------


def _migration():
    return importlib.import_module("faden_backend.db.migrations.versions.2026-10-12-10-00_pr0vk3ys")


async def test_migration_seals_plaintext_and_leaves_ciphertext(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mig = _migration()
    # An install's own key: the migration refuses the published development one (test below).
    monkeypatch.setattr(settings, "settings_master_key", "an-installs-own-master-key")
    already = seal_provider_key("sk-already")
    ids = {}
    for label, value in {"plain": KEY, "sealed": already, "null": None, "empty": ""}.items():
        provider = await ProviderDAO(session=dbsession).create(
            name=label, type_=ProviderType.CLOUD, target=ProviderTarget.REMOTE_ENDPOINT,
            capabilities={}, endpoint_url="https://x", extra_params={}, api_key_encrypted=value,
        )
        ids[label] = provider.id
    await dbsession.flush()

    conn = await dbsession.connection()

    def run(sync_conn) -> None:
        monkeypatch.setattr(mig.op, "get_bind", lambda: sync_conn)
        mig.upgrade()
        mig.upgrade()  # idempotent

    await conn.run_sync(run)

    assert open_provider_key(await _stored_key(dbsession, ids["plain"])) == KEY
    assert await _stored_key(dbsession, ids["sealed"]) == already
    assert await _stored_key(dbsession, ids["null"]) is None
    assert await _stored_key(dbsession, ids["empty"]) == ""


async def test_migration_without_master_key_fails_loudly(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mig = _migration()
    await ProviderDAO(session=dbsession).create(
        name="plain", type_=ProviderType.CLOUD, target=ProviderTarget.REMOTE_ENDPOINT,
        capabilities={}, endpoint_url="https://x", extra_params={}, api_key_encrypted=KEY,
    )
    await dbsession.flush()
    monkeypatch.setattr(settings, "settings_master_key", "")
    conn = await dbsession.connection()

    def run(sync_conn) -> None:
        monkeypatch.setattr(mig.op, "get_bind", lambda: sync_conn)
        with pytest.raises(RuntimeError):
            mig.upgrade()

    await conn.run_sync(run)


async def test_migration_refuses_the_published_master_key(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sealing with the key everyone knows would only look like encryption."""
    mig = _migration()
    monkeypatch.setattr(settings, "settings_master_key", "dev-settings-master-key-change-me")
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "allow_published_keys", False)
    with pytest.raises(RuntimeError, match="published development value"):
        mig._fernet()
    monkeypatch.setattr(settings, "allow_published_keys", True)  # kept on purpose until a rotation
    assert mig._fernet() is not None
