"""HTTPS at the edge: material goes to nginx, which is restarted; cookies follow the scheme.

nginx terminates TLS (llm_port_shared/nginx/select-config.sh serves HTTPS
whenever the install's ./tls holds edge.crt and edge.key), the backend
writes that directory through /api/admin/tls, and the session cookie is
Secure exactly when the page was served over HTTPS.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.models.users import User, cookie_transport, current_active_user
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import get_docker, get_root_mode_active
from llm_port_backend.web.middleware import SecureCookiesMiddleware, secured

TLS_URL = "/api/admin/tls"
PASSWORD = "correct horse battery staple"  # noqa: S105


def _material() -> tuple[bytes, bytes]:
    """A self-signed certificate and its key, as an operator would upload them."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "llm-port.test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ),
    )


def _files(cert: bytes, key: bytes) -> dict[str, tuple[str, bytes]]:
    return {"cert": ("edge.crt", cert), "key": ("edge.key", key)}


def _cookie_attributes(response: Any) -> list[str]:
    return [part.strip().lower() for part in response.headers["set-cookie"].split(";")]


class _Docker:
    """The containers on this host, and which of them were restarted."""

    def __init__(self, *names: str) -> None:
        self.names = names
        self.restarted: list[str] = []

    async def list_containers(self, all_: bool = True) -> list[dict[str, Any]]:
        return [{"Id": f"id-{name}", "Names": [f"/{name}"]} for name in self.names]

    async def restart(self, container_id: str, timeout: int = 10) -> None:
        self.restarted.append(container_id)


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> User:
    """An administrator in root mode, signed in."""
    user = User(
        email=f"admin-{uuid.uuid4().hex}@test.local",
        hashed_password=PasswordHelper().hash(PASSWORD),
        is_verified=True,
        is_active=True,
        is_superuser=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: True
    return user


def _https(fastapi_app: FastAPI) -> AsyncClient:
    """The same app reached over HTTPS, as it is behind nginx once uvicorn trusts X-Forwarded-Proto."""
    return AsyncClient(transport=ASGITransport(fastapi_app), base_url="https://test", timeout=2.0)


@pytest.mark.anyio
async def test_activating_material_hands_it_to_nginx_and_restarts_it(
    fastapi_app: FastAPI, admin: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tls_dir = tmp_path / "tls"
    monkeypatch.setattr(settings, "tls_edge_material_dir", str(tls_dir))
    docker = _Docker("llm-port-backend", "llm-port-nginx")
    fastapi_app.dependency_overrides[get_docker] = lambda: docker
    cert, key = _material()

    async with _https(fastapi_app) as client:
        uploaded = await client.post(f"{TLS_URL}/upload", files=_files(cert, key))
        assert uploaded.status_code == status.HTTP_200_OK, uploaded.text
        assert uploaded.json()["valid"] is True
        assert uploaded.json()["cert"]["is_self_signed"] is True

        activated = await client.post(f"{TLS_URL}/activate")
        assert activated.status_code == status.HTTP_200_OK, activated.text
        body = activated.json()
        assert body["proxy_restarted"] is True
        assert body["restart_required"] is False
        assert docker.restarted == ["id-llm-port-nginx"], "nginx, and only nginx"
        assert (tls_dir / "edge.crt").read_bytes() == cert
        assert (tls_dir / "edge.key").read_bytes() == key
        if sys.platform != "win32":
            assert (tls_dir / "edge.key").stat().st_mode & 0o777 == 0o600

        assert (await client.get(f"{TLS_URL}/status")).json()["has_activated_material"] is True

        removed = await client.delete(TLS_URL)
        assert removed.status_code == status.HTTP_204_NO_CONTENT
        assert not (tls_dir / "edge.crt").exists()
        assert not (tls_dir / "edge.key").exists()
        assert docker.restarted == ["id-llm-port-nginx"] * 2, "and again, so nginx goes back to plain HTTP"


@pytest.mark.anyio
async def test_without_the_proxy_container_the_operator_is_told_to_restart(
    fastapi_app: FastAPI, admin: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkout run without compose, or a proxy run elsewhere: the material is in place all the same."""
    monkeypatch.setattr(settings, "tls_edge_material_dir", str(tmp_path / "tls"))
    fastapi_app.dependency_overrides[get_docker] = lambda: _Docker("llm-port-backend")
    cert, key = _material()
    async with _https(fastapi_app) as client:
        await client.post(f"{TLS_URL}/upload", files=_files(cert, key))
        body = (await client.post(f"{TLS_URL}/activate")).json()
    assert body["proxy_restarted"] is False
    assert body["restart_required"] is True
    assert (tmp_path / "tls" / "edge.crt").exists()


@pytest.mark.anyio
async def test_a_private_key_is_not_taken_over_plain_http(client: AsyncClient, admin: User) -> None:
    """Which is why the backend must know the scheme behind nginx (FORWARDED_ALLOW_IPS)."""
    cert, key = _material()
    response = await client.post(f"{TLS_URL}/upload", files=_files(cert, key))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "plaintext" in response.json()["detail"]


@pytest.mark.anyio
async def test_the_session_cookie_is_secure_exactly_when_the_page_is(
    fastapi_app: FastAPI, client: AsyncClient, admin: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COOKIE_SECURE=false keeps login working over plain HTTP; over HTTPS the cookie is Secure anyway."""
    monkeypatch.setattr(cookie_transport, "cookie_secure", False)
    form = {"username": admin.email, "password": PASSWORD}

    plain = await client.post("/api/auth/cookie/login", data=form)
    assert plain.status_code in {status.HTTP_200_OK, status.HTTP_204_NO_CONTENT}, plain.text
    assert "secure" not in _cookie_attributes(plain)

    async with _https(fastapi_app) as https:
        served = await https.post("/api/auth/cookie/login", data=form)
    assert served.status_code in {status.HTTP_200_OK, status.HTTP_204_NO_CONTENT}, served.text
    assert _cookie_attributes(served).count("secure") == 1

    # A cookie the transport already marks Secure is not marked twice.
    monkeypatch.setattr(cookie_transport, "cookie_secure", True)
    async with _https(fastapi_app) as https:
        twice = await https.post("/api/auth/cookie/login", data=form)
    assert _cookie_attributes(twice).count("secure") == 1


def test_the_secure_attribute_is_added_once_whatever_the_case() -> None:
    assert secured(b"a=b; Path=/; HttpOnly") == b"a=b; Path=/; HttpOnly; Secure"
    assert secured(b"a=b; Path=/; secure") == b"a=b; Path=/; secure"
    assert secured(b"a=b; Secure; HttpOnly") == b"a=b; Secure; HttpOnly"


def test_the_middleware_is_part_of_the_app(fastapi_app: FastAPI) -> None:
    assert any(m.cls is SecureCookiesMiddleware for m in fastapi_app.user_middleware)
