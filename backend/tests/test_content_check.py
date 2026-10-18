"""Checking content for marks: for everyone signed in, and on the public page when an administrator offers it.

What the backend asks the engine (its verify and anchors routes, with the service token), how the engine's refusals
reach the person asking (its code kept), the limits (30 checks a minute per person, 10 per address on the public
page, 20 MB), texts kept for people with an account, and that nothing submitted is logged.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.rbac_dao import _DEFAULT_ROLES, RbacDAO
from faden_backend.db.models.system_settings import SystemSettingValue
from faden_backend.db.models.users import User, _signed_in_active_user
from faden_backend.services.provenance import checks
from faden_backend.services.provenance import client as provenance_client
from faden_backend.services.system_settings.registry import registry_by_key
from faden_backend.settings import settings

#: What a person submits; it must not reach any log.
SUBMITTED_TEXT = "Quarterly figures, confidential: revenue 4.2M, layoffs planned in Q3 -- marker-7f3a9c"
SERVICE_TOKEN = "the-token"  # noqa: S105 - the engine here is a fake
PICTURE = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"picture-bytes-marker-51d0" * 8).decode("ascii")

RESULT: dict[str, Any] = {
    "contract": "provenance/v1",
    "verdict": "ai-generated",
    "findings": [
        {
            "layer": "metadata", "method": "receipt-hash", "found": True,
            "detail": "This text is exactly what qwen3 generated on 2026-10-18.",
            "record_id": None, "receipt_id": "6f1c2b9e-0000-4000-8000-000000000001", "score": None,
        },
    ],
    "content_hash": "ab" * 32,
    "detector": "fadenstack-provenance/0.5.0",
    "checked_at": "2026-10-18T12:00:00Z",
    "signature": None,
}
ANCHORS = {
    "contract": "provenance/v1",
    "signing_chains": ["-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"],
    "trust_anchors": ["-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n"],
    "detector": {"id": "fadenstack-provenance/0.5.0", "methods": ["c2pa", "trustmark-Q"]},
}

VERIFY = "/api/provenance/verify"
STATUS = "/api/provenance/status"
PUBLIC = "/api/public/provenance"
PUBLIC_CHECK = "/api/public/provenance/check"
PUBLIC_ANCHORS = "/api/public/provenance/anchors"

pytestmark = pytest.mark.anyio


# ── The engine, answered here ─────────────────────────────────────────────────────


class Engine:
    """The engine's verify and anchors routes; records what it was asked."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.answer: Callable[[httpx.Request], httpx.Response] = self._ok

    def _ok(self, request: httpx.Request) -> httpx.Response:
        """The engine's result: the anchors, or a check (signed when asked)."""
        if request.url.path.endswith("/anchors"):
            return httpx.Response(200, json=ANCHORS)
        signed = json.loads(request.content).get("signed")
        return httpx.Response(200, json={**RESULT, "signature": "e.y.J" if signed else None})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Note the request and answer it."""
        self.seen.append(request)
        return self.answer(request)

    def bodies(self) -> list[dict[str, Any]]:
        """What it was asked to check."""
        return [json.loads(r.content) for r in self.seen if r.method == "POST"]


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    """The routes' engine calls go to :class:`Engine` through the real client (and its error mapping)."""
    fake = Engine()
    transport = httpx.MockTransport(fake)
    real_verify, real_anchors = provenance_client.verify, provenance_client.anchors

    async def verify(body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return await real_verify(body, base_url="http://faden-provenance:8000", token=SERVICE_TOKEN, transport=transport)

    async def anchors(**_: Any) -> dict[str, Any]:
        return await real_anchors(base_url="http://faden-provenance:8000", token=SERVICE_TOKEN, transport=transport)

    monkeypatch.setattr(provenance_client, "verify", verify)
    monkeypatch.setattr(provenance_client, "anchors", anchors)
    monkeypatch.setattr(settings, "provenance_enabled", True)
    return fake


@pytest.fixture(autouse=True)
def _fresh_limits() -> None:
    """Every test starts with no checks counted."""
    for limiter in (checks.USER_CHECKS, checks.PUBLIC_CHECKS, checks.PUBLIC_ANCHORS):
        limiter.reset()


async def _person(db: AsyncSession, *roles: str) -> User:
    user = User(
        id=uuid.uuid4(), email=f"person-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(user)
    await db.flush()
    rbac = RbacDAO(db)
    await rbac.seed_defaults()
    for name in roles:
        role = await rbac.get_role_by_name(name)
        assert role is not None
        await rbac.assign_role(user.id, role.id)
    await db.flush()
    return user


def _sign_in(app: FastAPI, user: User) -> None:
    app.dependency_overrides[_signed_in_active_user] = lambda: user


async def _offer_public_page(db: AsyncSession, *, contact: str = "") -> None:
    db.add(SystemSettingValue(key=checks.PUBLIC_CHECK_ENABLED_KEY, value_json={"value": True}))
    db.add(SystemSettingValue(key=checks.PUBLIC_CHECK_CONTACT_KEY, value_json={"value": contact}))
    await db.flush()


@pytest.fixture
async def visitor(fastapi_app: FastAPI) -> AsyncGenerator[Callable[[str], AsyncClient]]:
    """Clients without a sign-in, each from its own address."""
    opened: list[AsyncClient] = []

    def from_address(address: str) -> AsyncClient:
        c = AsyncClient(
            transport=ASGITransport(fastapi_app, client=(address, 50000)), base_url="http://test", timeout=5.0,
        )
        opened.append(c)
        return c

    yield from_address
    for c in opened:
        await c.aclose()


# ── The client ──────────────────────────────────────────────────────────────────────


async def test_the_engine_is_asked_to_verify_with_the_service_token() -> None:
    fake = Engine()
    result = await provenance_client.verify(
        {"text": "hello", "file": None, "media_type": None, "signed": True},
        base_url="http://faden-provenance:8000/", token=SERVICE_TOKEN, transport=httpx.MockTransport(fake),
    )
    (request,) = fake.seen
    assert str(request.url) == "http://faden-provenance:8000/api/v1/provenance/verify"
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer the-token"
    assert json.loads(request.content) == {"text": "hello", "file": None, "media_type": None, "signed": True}
    assert result["verdict"] == "ai-generated" and result["signature"] == "e.y.J"


async def test_the_engine_is_asked_for_its_anchors() -> None:
    fake = Engine()
    result = await provenance_client.anchors(base_url="http://x", token=SERVICE_TOKEN, transport=httpx.MockTransport(fake))
    assert [str(r.url) for r in fake.seen] == ["http://x/api/v1/provenance/anchors"]
    assert result == ANCHORS


def _refusal(status_code: int, code: str, message: str = "said the engine") -> httpx.Response:
    return httpx.Response(status_code, json={"detail": {"code": code, "message": message}})


@pytest.mark.parametrize(
    ("answer", "expected_status", "expected_code"),
    [
        (_refusal(503, "no_signing_key"), 503, "no_signing_key"),
        (_refusal(503, "busy"), 503, "busy"),
        (_refusal(503, "unavailable"), 503, "unavailable"),
        (httpx.Response(503, json={"detail": "The provenance service has no service token set."}), 503, "unavailable"),
        (_refusal(413, "too_large"), 413, "too_large"),
        (_refusal(422, "not-base64"), 422, "not-base64"),
        (httpx.Response(422, json={"detail": [{"loc": ["body", "x"], "msg": "Extra inputs"}]}), 422, "invalid"),
        (httpx.Response(401, json={"detail": "Missing service token."}), 502, "refused"),
        (httpx.Response(403, json={"detail": "Invalid service token."}), 502, "refused"),
        (httpx.Response(404, json={"detail": "Not Found"}), 502, "unexpected"),
        (httpx.Response(500, text="Internal Server Error"), 502, "unexpected"),
        (httpx.Response(200, text="<html>a proxy page</html>"), 502, "unexpected"),
        (httpx.Response(200, json={"status": "ok"}), 502, "unexpected"),
    ],
)
async def test_the_engines_refusals_become_errors_with_its_code(
    answer: httpx.Response, expected_status: int, expected_code: str,
) -> None:
    with pytest.raises(provenance_client.EngineError) as refused:
        await provenance_client.verify(
            {"text": "t"}, base_url="http://x", token=SERVICE_TOKEN, transport=httpx.MockTransport(lambda _r: answer),
        )
    assert (refused.value.status_code, refused.value.code) == (expected_status, expected_code)
    assert refused.value.message


async def test_the_engines_own_message_is_kept() -> None:
    answer = _refusal(503, "busy", "The detection lane is full; try again.")
    with pytest.raises(provenance_client.EngineError) as refused:
        await provenance_client.verify({}, base_url="http://x", token=SERVICE_TOKEN, transport=httpx.MockTransport(lambda _r: answer))
    assert refused.value.detail() == {"code": "busy", "message": "The detection lane is full; try again."}


async def test_an_engine_that_does_not_answer_is_unavailable_or_too_slow() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    for handler, expected in ((down, (503, "unavailable")), (slow, (504, "timeout"))):
        with pytest.raises(provenance_client.EngineError) as refused:
            await provenance_client.verify({}, base_url="http://x", token=SERVICE_TOKEN, transport=httpx.MockTransport(handler))
        assert (refused.value.status_code, refused.value.code) == expected


# ── Checking, signed in ────────────────────────────────────────────────────────────


async def test_every_built_in_role_may_check_content() -> None:
    for name, permissions in _DEFAULT_ROLES.items():
        assert "check" in permissions.get("provenance", []), name


@pytest.mark.parametrize("role", ["default_user", "viewer", "auditor"])
async def test_a_signed_in_person_checks_a_text(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine, role: str,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, role))
    r = await client.post(VERIFY, json={"text": SUBMITTED_TEXT})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json() == RESULT
    assert engine.bodies() == [{"text": SUBMITTED_TEXT, "file": None, "media_type": None, "signed": False}]
    assert engine.seen[0].headers["Authorization"] == "Bearer the-token"


async def test_a_signed_in_person_checks_a_file_and_gets_it_signed(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    r = await client.post(VERIFY, json={"file": PICTURE, "media_type": " Image/PNG ", "signed": True})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["signature"] == "e.y.J"
    assert engine.bodies() == [{"text": None, "file": PICTURE, "media_type": "image/png", "signed": True}]


async def test_the_status_says_whether_content_can_be_checked(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    for enabled in (True, False):
        monkeypatch.setattr(settings, "provenance_enabled", enabled)
        r = await client.get(STATUS)
        assert r.status_code == status.HTTP_200_OK, r.text
        assert r.json() == {"enabled": enabled, "max_bytes": 20 * 1024 * 1024}


async def test_checking_needs_the_right(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession))  # no role at all
    for r in (await client.post(VERIFY, json={"text": "x"}), await client.get(STATUS)):
        assert r.status_code == status.HTTP_403_FORBIDDEN
    assert engine.seen == []


async def test_checking_needs_a_sign_in(client: AsyncClient, engine: Engine) -> None:
    r = await client.post(VERIFY, json={"text": "x"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    assert engine.seen == []


async def test_nothing_is_checked_while_the_module_is_off(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    monkeypatch.setattr(settings, "provenance_enabled", False)
    r = await client.post(VERIFY, json={"text": "x"})
    assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert r.json()["detail"]["code"] == "module_off"
    assert engine.seen == []


async def test_a_person_checks_30_times_a_minute(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
) -> None:
    first, second = await _person(dbsession, "default_user"), await _person(dbsession, "default_user")
    _sign_in(fastapi_app, first)
    for _ in range(30):
        assert (await client.post(VERIFY, json={"text": "x"})).status_code == status.HTTP_200_OK
    r = await client.post(VERIFY, json={"text": "x"})
    assert r.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert r.json()["detail"]["code"] == "rate_limited"
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    assert len(engine.seen) == 30

    _sign_in(fastapi_app, second)  # someone else is counted on their own
    assert (await client.post(VERIFY, json={"text": "x"})).status_code == status.HTTP_200_OK


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "nothing-to-check"),
        ({"text": "a", "file": "YQ=="}, "nothing-to-check"),
        ({"text": 5}, "invalid"),
        ({"text": "a", "extra": SUBMITTED_TEXT}, "invalid"),
    ],
)
async def test_a_body_that_is_not_a_check_is_refused(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
    body: dict[str, Any], code: str,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    r = await client.post(VERIFY, json=body)
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, r.text
    assert r.json()["detail"]["code"] == code
    assert SUBMITTED_TEXT not in r.text, "a refusal does not repeat what was sent"
    assert engine.seen == []


async def test_a_text_that_is_not_unicode_is_refused(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    r = await client.post(VERIFY, content=b'{"text": "a\\ud800b"}', headers={"Content-Type": "application/json"})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, r.text
    assert r.json()["detail"]["code"] == "invalid"
    assert engine.seen == []


async def test_a_body_that_is_not_json_is_refused(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    r = await client.post(VERIFY, content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert r.json()["detail"]["code"] == "invalid"


async def test_content_over_the_size_limit_is_not_sent(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    monkeypatch.setattr(checks, "MAX_CONTENT_BYTES", 30)
    monkeypatch.setattr(checks, "MAX_FILE_BASE64", 44)
    monkeypatch.setattr(checks, "MAX_BODY_BYTES", 400)

    assert (await client.post(VERIFY, json={"text": "ä" * 15})).status_code == status.HTTP_200_OK  # 30 bytes
    for body in ({"text": "ä" * 16}, {"file": "A" * 48}, {"text": "a" * 500}):
        r = await client.post(VERIFY, json=body)
        assert r.status_code == status.HTTP_413_CONTENT_TOO_LARGE, r.text
        assert r.json()["detail"]["code"] == "too_large"
    assert len(engine.seen) == 1


async def test_a_declared_length_over_the_limit_is_refused_before_reading(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    r = await client.post(
        VERIFY, content=b"{}", headers={"Content-Type": "application/json", "Content-Length": str(10**9)},
    )
    assert r.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert engine.seen == []


@pytest.mark.parametrize(
    ("answer", "expected_status", "expected_code"),
    [
        (_refusal(503, "no_signing_key"), 503, "no_signing_key"),
        (_refusal(503, "busy"), 503, "busy"),
        (_refusal(413, "too_large"), 413, "too_large"),
        (_refusal(422, "not-base64"), 422, "not-base64"),
        (httpx.Response(403, json={"detail": "Invalid service token."}), 502, "refused"),
        (httpx.Response(500, text="boom"), 502, "unexpected"),
    ],
)
async def test_the_engines_refusals_reach_the_person_with_its_code(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
    answer: httpx.Response, expected_status: int, expected_code: str,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    engine.answer = lambda _r: answer
    r = await client.post(VERIFY, json={"file": PICTURE, "media_type": "image/png"})
    assert r.status_code == expected_status, r.text
    assert r.json()["detail"]["code"] == expected_code
    assert r.json()["detail"]["message"]


async def test_an_engine_that_is_down_is_a_clear_answer(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    engine.answer = down
    r = await client.post(VERIFY, json={"text": "x"})
    assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert r.json()["detail"] == {"code": "unavailable", "message": "The provenance engine is not answering."}


async def test_nothing_submitted_is_logged(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "default_user"))
    picture_marker = base64.b64decode(PICTURE)[8:40].decode("ascii")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused while sending {request.content[:20]!r}", request=request)

    with caplog.at_level(logging.DEBUG):
        await client.post(VERIFY, json={"text": SUBMITTED_TEXT})
        await client.post(VERIFY, json={"file": PICTURE, "media_type": "image/png"})
        engine.answer = lambda _r: _refusal(503, "busy")
        await client.post(VERIFY, json={"text": SUBMITTED_TEXT})
        engine.answer = down
        await client.post(VERIFY, json={"text": SUBMITTED_TEXT})
        await client.post(VERIFY, json={"text": SUBMITTED_TEXT, "unknown": SUBMITTED_TEXT})

    assert caplog.records, "the requests were logged at all"
    for marker in (SUBMITTED_TEXT, "marker-7f3a9c", PICTURE, picture_marker):
        assert marker not in caplog.text


# ── The public page ────────────────────────────────────────────────────────────────


def test_the_public_page_is_off_by_default() -> None:
    by_key = registry_by_key()
    enabled = by_key[checks.PUBLIC_CHECK_ENABLED_KEY]
    assert enabled.type == "bool" and enabled.default is False
    assert enabled.category == "modules" and enabled.group == "provenance"
    contact = by_key[checks.PUBLIC_CHECK_CONTACT_KEY]
    assert contact.type == "string" and contact.default == ""


async def test_every_public_route_is_not_found_while_the_page_is_off(
    visitor: Callable[[str], AsyncClient], engine: Engine,
) -> None:
    c = visitor("203.0.113.5")
    for r in (
        await c.get(PUBLIC),
        await c.post(PUBLIC_CHECK, json={"file": PICTURE, "media_type": "image/png"}),
        await c.get(PUBLIC_ANCHORS),
    ):
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert r.json() == {"detail": "Not Found"}
    assert engine.seen == []


async def test_the_public_page_is_not_found_while_the_module_is_off(
    visitor: Callable[[str], AsyncClient], dbsession: AsyncSession, engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _offer_public_page(dbsession)
    monkeypatch.setattr(settings, "provenance_enabled", False)
    c = visitor("203.0.113.5")
    assert (await c.get(PUBLIC)).status_code == status.HTTP_404_NOT_FOUND
    assert (await c.post(PUBLIC_CHECK, json={"file": PICTURE})).status_code == status.HTTP_404_NOT_FOUND
    assert engine.seen == []


async def test_anyone_checks_a_picture_when_the_page_is_offered(
    visitor: Callable[[str], AsyncClient], dbsession: AsyncSession, engine: Engine,
) -> None:
    await _offer_public_page(dbsession, contact="  provenance@example.org  ")
    c = visitor("203.0.113.5")

    r = await c.get(PUBLIC)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json() == {"contact": "provenance@example.org", "max_bytes": 20 * 1024 * 1024}

    r = await c.post(PUBLIC_CHECK, json={"file": PICTURE, "media_type": "image/png", "signed": True})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["verdict"] == "ai-generated" and r.json()["signature"] == "e.y.J"
    assert engine.bodies() == [{"text": None, "file": PICTURE, "media_type": "image/png", "signed": True}]

    r = await c.get(PUBLIC_ANCHORS)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json() == ANCHORS


@pytest.mark.parametrize(
    "body",
    [
        {"text": SUBMITTED_TEXT},
        {"file": base64.b64encode(SUBMITTED_TEXT.encode()).decode(), "media_type": "text/plain"},
        {"file": base64.b64encode(b"# notes").decode(), "media_type": "Text/Markdown"},
    ],
)
async def test_texts_are_refused_on_the_public_page_naming_whom_to_ask(
    visitor: Callable[[str], AsyncClient], dbsession: AsyncSession, engine: Engine, body: dict[str, Any],
) -> None:
    await _offer_public_page(dbsession, contact="provenance@example.org")
    r = await visitor("203.0.113.5").post(PUBLIC_CHECK, json=body)
    assert r.status_code == status.HTTP_403_FORBIDDEN, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "text_not_public"
    assert detail["contact"] == "provenance@example.org"
    assert "provenance@example.org" in detail["message"]
    assert SUBMITTED_TEXT not in r.text
    assert engine.seen == []


async def test_an_address_checks_10_times_a_minute_on_the_public_page(
    visitor: Callable[[str], AsyncClient], dbsession: AsyncSession, engine: Engine,
) -> None:
    await _offer_public_page(dbsession)
    first = visitor("203.0.113.5")
    for _ in range(10):
        r = await first.post(PUBLIC_CHECK, json={"file": PICTURE, "media_type": "image/png"})
        assert r.status_code == status.HTTP_200_OK, r.text
    r = await first.post(PUBLIC_CHECK, json={"file": PICTURE, "media_type": "image/png"})
    assert r.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert r.json()["detail"]["code"] == "rate_limited"
    assert "Retry-After" in r.headers
    assert len(engine.seen) == 10

    other = visitor("198.51.100.7")
    assert (await other.post(PUBLIC_CHECK, json={"file": PICTURE})).status_code == status.HTTP_200_OK


async def test_the_public_page_maps_the_engines_refusals(
    visitor: Callable[[str], AsyncClient], dbsession: AsyncSession, engine: Engine,
) -> None:
    await _offer_public_page(dbsession)
    engine.answer = lambda _r: _refusal(503, "busy")
    c = visitor("203.0.113.5")
    r = await c.post(PUBLIC_CHECK, json={"file": PICTURE, "media_type": "image/png"})
    assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert r.json()["detail"]["code"] == "busy"
    r = await c.get(PUBLIC_ANCHORS)
    assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


async def test_nothing_submitted_on_the_public_page_is_logged(
    visitor: Callable[[str], AsyncClient], dbsession: AsyncSession, engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _offer_public_page(dbsession)
    c = visitor("203.0.113.5")
    with caplog.at_level(logging.DEBUG):
        await c.post(PUBLIC_CHECK, json={"file": PICTURE, "media_type": "image/png"})
        await c.post(PUBLIC_CHECK, json={"text": SUBMITTED_TEXT})
        engine.answer = lambda _r: httpx.Response(500, text="boom")
        await c.post(PUBLIC_CHECK, json={"file": PICTURE, "media_type": "image/png"})
    for marker in (SUBMITTED_TEXT, PICTURE):
        assert marker not in caplog.text


# ── The limits ───────────────────────────────────────────────────────────────────────


def test_the_limit_slides_with_the_window() -> None:
    now = [1000.0]
    limiter = checks.RateLimiter(2, 60.0, clock=lambda: now[0])
    assert limiter.hit("a") == 0 and limiter.hit("a") == 0
    assert limiter.hit("a") == pytest.approx(60.0)
    now[0] += 30
    assert limiter.hit("a") == pytest.approx(30.0)
    assert limiter.hit("b") == 0, "each key on its own"
    now[0] += 30.5
    assert limiter.hit("a") == 0, "the first hits are out of the window"


def test_the_limit_holds_a_bounded_number_of_keys() -> None:
    now = [0.0]
    limiter = checks.RateLimiter(1, 60.0, max_keys=3, clock=lambda: now[0])
    for key in ("a", "b", "c"):
        limiter.hit(key)
    now[0] += 61
    limiter.hit("d")  # the stale keys are forgotten first
    assert len(limiter) == 1 and "d" in limiter
    for key in ("e", "f", "g"):
        limiter.hit(key)
    assert len(limiter) == 3 and "d" not in limiter


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("203.0.113.5", "203.0.113.5"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:bbbb::9", "2001:db8:1:2::/64"),
        ("::ffff:198.51.100.7", "198.51.100.7"),
        ("testclient", "testclient"),
    ],
)
def test_a_public_request_counts_against_its_address(address: str, key: str) -> None:
    class Request:
        client = type("Client", (), {"host": address})()

    assert checks.client_key(Request()) == key  # type: ignore[arg-type]
