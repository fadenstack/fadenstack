"""Signing an app in by pairing (RFC 8628): the app shows a code, a person approves it in the console.

The app starts a pairing without signing in and polls with its pairing code; the person looks the user code
up, approves or denies it; the app gets the approving person's agent tokens once. Wrong codes are limited
per person and per address, starting per address. Neither code is stored in the clear or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from loguru import logger as loguru_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.agents import (
    AgentPairing,
    AgentPairingFailure,
    AgentPairingStatus,
    AgentRefreshToken,
)
from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.agents import pairing
from faden_backend.settings import settings

BASE = "/api/agent-tokens"
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "faden_backend" / "db" / "migrations" / "versions" / "2026-10-30-12-00_p41r1ng.py"
)


class _As:
    """Who the API sees as signed in to the console; switched within a test."""

    def __init__(self) -> None:
        self.user: User | None = None

    def __call__(self) -> User:
        assert self.user is not None
        return self.user


async def _user(db: AsyncSession, *, admin: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="x",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
def signed_in(fastapi_app: FastAPI) -> _As:
    who = _As()
    fastapi_app.dependency_overrides[current_active_user] = who
    return who


@pytest.fixture
def secret() -> str:
    if not settings.users_secret:
        object.__setattr__(settings, "users_secret", "test-agent-secret-0123456789abcdef")
    return settings.users_secret


@pytest.fixture
async def far_client(fastapi_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client on another network (203.0.113.9) than :func:`client` (127.0.0.1)."""
    async with AsyncClient(
        transport=ASGITransport(fastapi_app, client=("203.0.113.9", 40000)), base_url="http://test", timeout=2.0,
    ) as ac:
        yield ac


async def _agent(client: AsyncClient, slug: str, *granted: User) -> None:
    """A published agent, granted to *granted* (the signed-in admin creates it)."""
    r = await client.post("/api/agents", json={"slug": slug, "name": slug.title(), "version": {}})
    assert r.status_code == status.HTTP_201_CREATED, r.text
    r = await client.post(f"/api/agents/{slug}/versions/1/publish")
    assert r.status_code == status.HTTP_200_OK, r.text
    await _grant(client, slug, *granted)


async def _grant(client: AsyncClient, slug: str, *users: User) -> None:
    r = await client.put(
        f"/api/agents/{slug}/grants",
        json={"grants": [{"principal_type": "user", "principal_id": str(u.id)} for u in users]},
    )
    assert r.status_code == status.HTTP_200_OK, r.text


async def _start(client: AsyncClient, agent: str = "desk", device: str = "Desktop agent on SACHI-PC") -> Any:
    r = await client.post(
        f"{BASE}/pairings",
        json={
            "agent": agent,
            "device_label": device,
            "client": {"name": "Fadenstack Desktop", "version": "0.1.0", "os": "Windows 11"},
        },
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    return r.json()


async def _poll(client: AsyncClient, started: dict[str, Any]) -> Response:
    return await client.post(f"{BASE}/pairings/token", json={"pairing_code": started["pairing_code"]})


async def _console(client: AsyncClient, step: str, code: str) -> Response:
    return await client.post(f"{BASE}/pairings/{step}", json={"code": code})


async def _waited(db: AsyncSession) -> None:
    """As if the app had waited its interval since its last poll."""
    for row in (await db.execute(select(AgentPairing))).scalars():
        if row.last_polled_at is not None:
            row.last_polled_at -= timedelta(seconds=row.interval_s + 1)
    await db.flush()


async def _pairing_row(db: AsyncSession, started: dict[str, Any]) -> AgentPairing:
    digest = hashlib.sha256(started["pairing_code"].encode()).hexdigest()
    return (await db.execute(select(AgentPairing).where(AgentPairing.pairing_code_hash == digest))).scalar_one()


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    return list((await db.execute(select(AuditEvent).where(AuditEvent.action == action))).scalars())


# -- the codes ------------------------------------------------------------------------------


def test_user_codes_follow_rfc_8628_and_are_normalised() -> None:
    for _ in range(200):
        code = pairing.new_user_code()
        assert len(code) == 8 and set(code) <= set("BCDFGHJKLMNPQRSTVWXZ")
    assert pairing.shown("BDWPHTQX") == "BDWP-HTQX"
    for typed in ("BDWP-HTQX", "bdwp-htqx", " bdwp htqx ", "BdWpHtQx", "BDWP--HTQX"):
        assert pairing.normalise(typed) == "BDWPHTQX"
    # Never generated, so never a match: vowels, digits (0/O, 1/I), the wrong length.
    for wrong in ("BDWP-HTQ0", "ODWP-HTQX", "BDWP-HTQ", "BDWP-HTQXB", "", "----"):
        assert pairing.normalise(wrong) is None


def test_addresses_count_by_their_network() -> None:
    assert pairing.address("::ffff:198.51.100.7") == "198.51.100.7"
    assert pairing.address("not an address") is None
    assert pairing.same_network("198.51.100.7", "198.51.100.7")
    assert not pairing.same_network("198.51.100.7", "198.51.100.8")
    assert pairing.same_network("2001:db8:1:2::5", "2001:db8:1:2:aaaa::9")
    assert not pairing.same_network("2001:db8:1:2::5", "2001:db8:1:3::5")
    assert not pairing.same_network(None, "198.51.100.7")


# -- the flow ---------------------------------------------------------------------------------


def _as_rfc_8628_says(r: Response) -> Any:
    """The start's answer (RFC 8628 §3.2), checked."""
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert r.headers["cache-control"] == "no-store"
    started = r.json()
    assert started["expires_in"] == 600 and started["interval"] == 5
    assert len(started["pairing_code"]) >= 43
    code = started["user_code"]
    assert len(code) == 9 and code[4] == "-" and pairing.normalise(code) == code.replace("-", "")
    assert started["verification_uri"] == "http://test/pair"
    # The code rides in the fragment, which browsers never send: no access log sees it.
    assert started["verification_uri_complete"] == f"http://test/pair#code={code}"
    return started


async def _audited(db: AsyncSession, row: AgentPairing, person: User, tokens: dict[str, Any]) -> None:
    """The trail of a pairing that signed *person* in: by the pairing's id, never a code."""
    [start] = await _events(db, "agent_pairing.start")
    assert start.target_id == str(row.id) and start.actor_id is None
    [approve] = await _events(db, "agent_pairing.approve")
    assert approve.actor_id == person.id and approve.target_id == str(row.id)
    assert json.loads(approve.metadata_json or "{}")["agent"] == "desk"
    [issue] = await _events(db, "agent_token.issue")
    assert issue.actor_id == person.id
    assert json.loads(issue.metadata_json or "{}") == {"device": tokens["device_id"], "pairing": str(row.id)}


async def test_an_app_is_signed_in_once_the_person_approves(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    anna = await _user(dbsession)
    await _agent(client, "desk", anna)

    started = _as_rfc_8628_says(await client.post(
        f"{BASE}/pairings",
        json={"agent": "desk", "device_label": "Desktop agent on SACHI-PC",
              "client": {"name": "Fadenstack Desktop", "version": "0.1.0", "os": "Windows 11"}},
    ))
    code = started["user_code"]
    r = await _poll(client, started)
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.json()["error"] == "authorization_pending"
    assert r.headers["cache-control"] == "no-store"

    # The person, in the console, types the code as it comes.
    signed_in.user = anna
    typed = code.replace("-", " ").lower()
    r = await _console(client, "lookup", typed)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.headers["cache-control"] == "no-store"
    seen = r.json()
    assert seen["agent"] == {"slug": "desk", "name": "Desk"} and seen["available"] is True
    assert seen["device_label"] == "Desktop agent on SACHI-PC"
    assert seen["client"] == {"name": "Fadenstack Desktop", "version": "0.1.0", "os": "Windows 11"}
    assert seen["requester_ip"] == "127.0.0.1" and seen["same_network"] is True
    assert seen["sign_in_lasts"] == {
        "idle_days": round(settings.agent_refresh_idle_s / 86400, 2),
        "max_days": round(settings.agent_refresh_token_ttl_s / 86400, 2),
    }

    r = await _console(client, "approve", typed)
    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text
    assert r.headers["cache-control"] == "no-store"

    await _waited(dbsession)
    r = await _poll(client, started)
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert r.headers["cache-control"] == "no-store"
    tokens = r.json()
    assert tokens["agent"] == "desk"
    assert tokens["user"] == {"id": str(anna.id), "email": anna.email, "display_name": None}
    agent_id = tokens["agent_id"]
    claims = jwt.decode(tokens["access_token"], secret, algorithms=["HS256"], audience=f"agent:{agent_id}")
    assert claims["sub"] == str(anna.id) and claims["did"] == tokens["device_id"]
    # A sign-in like any other: in My sign-ins, with the pairing's device label; it refreshes.
    mine = (await client.get(f"{BASE}/mine")).json()
    assert [(s["device_id"], s["device_label"]) for s in mine] == [(tokens["device_id"], "Desktop agent on SACHI-PC")]
    r = await client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == status.HTTP_200_OK and r.json()["user"]["id"] == str(anna.id)

    # Once: the pairing is consumed.
    await _waited(dbsession)
    r = await _poll(client, started)
    assert r.status_code == status.HTTP_400_BAD_REQUEST and r.json()["error"] == "invalid_grant"
    assert (await _pairing_row(dbsession, started)).status == AgentPairingStatus.CONSUMED
    r = await _console(client, "approve", code)
    assert r.status_code == status.HTTP_409_CONFLICT
    await _audited(dbsession, await _pairing_row(dbsession, started), anna, tokens)


async def test_the_normal_sign_in_names_the_person_too(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    anna = await _user(dbsession)
    await _agent(client, "named", anna)
    signed_in.user = anna
    r = await client.post(BASE, json={"agent": "named", "device_label": "Word"})
    assert r.status_code == status.HTTP_201_CREATED
    assert r.json()["user"] == {"id": str(anna.id), "email": anna.email, "display_name": None}


async def test_polling_too_often_slows_the_app_down(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    started = await _start(client)
    assert (await _poll(client, started)).json()["error"] == "authorization_pending"
    r = await _poll(client, started)
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.json() == {
        "error": "slow_down", "error_description": "Polled too often; wait 10 s between polls.", "interval": 10,
    }
    assert (await _poll(client, started)).json()["interval"] == 15
    row = await _pairing_row(dbsession, started)
    assert (row.interval_s, row.failed_polls) == (15, 2)
    # Waiting the new interval is on time again.
    await _waited(dbsession)
    assert (await _poll(client, started)).json()["error"] == "authorization_pending"


async def test_an_expired_pairing_gives_nothing(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    anna = await _user(dbsession)
    await _agent(client, "desk", anna)
    started = await _start(client)
    row = await _pairing_row(dbsession, started)
    assert row.expires_at - row.created_at == timedelta(seconds=600)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await dbsession.flush()

    signed_in.user = anna
    for step in ("lookup", "approve", "deny"):
        r = await _console(client, step, started["user_code"])
        assert r.status_code == status.HTTP_404_NOT_FOUND, step
        assert r.json()["detail"]["code"] == "unknown_code"
    r = await _poll(client, started)
    assert r.status_code == status.HTTP_400_BAD_REQUEST and r.json()["error"] == "expired_token"
    assert row.status == AgentPairingStatus.EXPIRED
    # Each expired try counted as a failure.
    assert len((await dbsession.execute(select(AgentPairingFailure))).scalars().all()) == 3


async def test_a_denied_pairing_tells_the_app(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    anna = signed_in.user = await _user(dbsession)
    started = await _start(client)
    r = await _console(client, "deny", started["user_code"])
    assert r.status_code == status.HTTP_204_NO_CONTENT
    assert r.headers["cache-control"] == "no-store"
    r = await _poll(client, started)
    assert r.status_code == status.HTTP_400_BAD_REQUEST and r.json()["error"] == "access_denied"
    r = await _console(client, "lookup", started["user_code"])
    assert r.status_code == status.HTTP_409_CONFLICT and r.json()["detail"]["code"] == "already_decided"
    row = await _pairing_row(dbsession, started)
    assert (row.status, row.approved_by) == (AgentPairingStatus.DENIED, anna.id)
    [deny] = await _events(dbsession, "agent_pairing.deny")
    assert deny.actor_id == anna.id and deny.target_id == str(row.id)
    assert (await dbsession.execute(select(AgentRefreshToken))).first() is None


async def test_an_unknown_pairing_code_is_an_invalid_grant(client: AsyncClient, secret: str) -> None:
    r = await client.post(f"{BASE}/pairings/token", json={"pairing_code": "x" * 43})
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.json()["error"] == "invalid_grant"


# -- access -----------------------------------------------------------------------------------


async def test_approving_takes_access_to_the_agent_and_says_no_more_than_that(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    anna, ben = await _user(dbsession), await _user(dbsession)
    await _agent(client, "desk", anna)
    await _agent(client, "old", ben)
    r = await client.post("/api/agents/old/retire")
    assert r.status_code == status.HTTP_200_OK

    refusals = []
    for agent in ("desk", "old", "no-such-agent"):  # not granted, retired, unknown
        signed_in.user = admin
        started = await _start(client, agent)
        signed_in.user = ben
        seen = (await _console(client, "lookup", started["user_code"])).json()
        assert seen["available"] is False and seen["agent"] == {"slug": agent, "name": None}
        r = await _console(client, "approve", started["user_code"])
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert r.headers["cache-control"] == "no-store"
        refusals.append(r.json())
        # The pairing stays open; the app keeps waiting (or the person denies it).
        assert (await _poll(client, started)).json()["error"] == "authorization_pending"
    assert refusals == [
        {"detail": {"code": "agent_not_available", "message": "This agent is not available to you."}},
    ] * 3
    reasons = [json.loads(e.metadata_json or "{}")["reason"] for e in await _events(dbsession, "agent_pairing.approve")]
    assert sorted(reasons) == ["agent_retired", "no_grant", "unknown_agent"]


async def test_access_lost_after_the_approval_gives_no_tokens(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    anna = await _user(dbsession)
    await _agent(client, "desk", anna)
    started = await _start(client)
    signed_in.user = anna
    assert (await _console(client, "approve", started["user_code"])).status_code == status.HTTP_204_NO_CONTENT
    signed_in.user = admin
    await _grant(client, "desk")
    r = await _poll(client, started)
    assert r.status_code == status.HTTP_400_BAD_REQUEST and r.json()["error"] == "access_denied"
    assert (await _pairing_row(dbsession, started)).status == AgentPairingStatus.DENIED


async def test_a_page_of_another_site_cannot_approve(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    """The console's CSRF check covers the pairing routes as every other POST."""
    signed_in.user = await _user(dbsession)
    started = await _start(client)
    r = await client.post(
        f"{BASE}/pairings/approve",
        json={"code": started["user_code"]},
        headers={"Origin": "https://evil.example", "Cookie": "fapiauth=session"},
    )
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert (await _pairing_row(dbsession, started)).status == AgentPairingStatus.PENDING


# -- limits -----------------------------------------------------------------------------------


async def test_wrong_codes_are_limited_per_person_without_locking_the_account(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    anna, ben = await _user(dbsession), await _user(dbsession)
    await _agent(client, "desk", anna, ben)
    started = await _start(client)

    signed_in.user = anna
    for step in ("lookup", "approve", "deny", "lookup", "approve"):
        assert (await _console(client, step, "BBBB-BBBB")).status_code == status.HTTP_404_NOT_FOUND
    # Over the limit: even the right code waits, with the time to wait.
    r = await _console(client, "approve", started["user_code"])
    assert r.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert r.headers["cache-control"] == "no-store"
    wait = int(r.headers["retry-after"])
    assert 590 <= wait <= 600 and r.json()["detail"]["retry_after"] == wait
    # The account is not locked: Anna still signs in to the agent, and another person still pairs.
    r = await client.post(BASE, json={"agent": "desk", "device_label": "Word"})
    assert r.status_code == status.HTTP_201_CREATED
    signed_in.user = ben
    assert (await _console(client, "approve", started["user_code"])).status_code == status.HTTP_204_NO_CONTENT
    # Ten minutes on, Anna may try again.
    for failure in (await dbsession.execute(select(AgentPairingFailure))).scalars():
        failure.failed_at -= timedelta(seconds=601)
    await dbsession.flush()
    signed_in.user = anna
    assert (await _console(client, "lookup", "BBBB-BBBB")).status_code == status.HTTP_404_NOT_FOUND


async def test_wrong_codes_are_limited_per_address(
    signed_in: _As, client: AsyncClient, far_client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    people = [await _user(dbsession) for _ in range(5)]
    for person in people[:4]:
        signed_in.user = person
        for _ in range(5):
            assert (await _console(client, "lookup", "CCCC-CCCC")).status_code == status.HTTP_404_NOT_FOUND
    signed_in.user = people[4]
    r = await _console(client, "lookup", "CCCC-CCCC")
    assert r.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert int(r.headers["retry-after"]) > 0
    # The same person from another address is not held back.
    assert (await _console(far_client, "lookup", "CCCC-CCCC")).status_code == status.HTTP_404_NOT_FOUND


async def test_starting_is_limited_per_address(
    client: AsyncClient, far_client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    for _ in range(10):
        await _start(client)
    r = await client.post(f"{BASE}/pairings", json={"agent": "desk", "device_label": "PC"})
    assert r.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert r.headers["cache-control"] == "no-store"
    assert 590 <= int(r.headers["retry-after"]) <= 600
    await _start(far_client)


async def test_a_request_from_another_network_is_flagged(
    signed_in: _As, client: AsyncClient, far_client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    started = await _start(far_client)
    signed_in.user = await _user(dbsession)
    seen = (await _console(client, "lookup", started["user_code"])).json()
    assert seen["requester_ip"] == "203.0.113.9" and seen["same_network"] is False


async def test_old_pairings_are_deleted_when_a_new_one_starts(
    client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    started = await _start(client)
    old = await _pairing_row(dbsession, started)
    old.created_at -= timedelta(days=1, seconds=1)
    old.expires_at -= timedelta(days=1, seconds=1)
    await dbsession.flush()
    await _start(client)
    left = (await dbsession.execute(select(AgentPairing.id))).scalars().all()
    assert old.id not in left and len(left) == 1


async def test_what_the_app_says_must_show_as_it_is(client: AsyncClient, secret: str) -> None:
    for label in ("Desktop agent on \u202eCP-IHCAS", "PC\u200b", "PC\n2"):
        r = await client.post(f"{BASE}/pairings", json={"agent": "desk", "device_label": label})
        assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, label
    r = await client.post(
        f"{BASE}/pairings", json={"agent": "desk", "device_label": "PC", "client": {"name": "App\u2066"}},
    )
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    r = await client.post(f"{BASE}/pairings", json={"agent": "../desk", "device_label": "PC"})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# -- secrets ----------------------------------------------------------------------------------


async def test_codes_are_kept_hashed_and_never_logged(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str, caplog: pytest.LogCaptureFixture,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    anna = await _user(dbsession)
    await _agent(client, "desk", anna)
    loguru_lines: list[str] = []
    sink = loguru_logger.add(lambda message: loguru_lines.append(str(message)), level="DEBUG")
    caplog.set_level(logging.DEBUG)
    try:
        started = await _start(client)
        signed_in.user = anna
        await _console(client, "lookup", started["user_code"])
        await _console(client, "lookup", "DDDD-DDDD")
        await _console(client, "approve", started["user_code"])
        await _waited(dbsession)
        tokens = (await _poll(client, started)).json()
        await _poll(client, started)
        assert tokens["user"]["id"] == str(anna.id)
    finally:
        loguru_logger.remove(sink)

    user_code = started["user_code"]
    plain = user_code.replace("-", "")
    secrets_ = [started["pairing_code"], user_code, plain, "DDDDDDDD", "DDDD-DDDD"]
    logged = caplog.text + "".join(loguru_lines)
    assert logged, "the flow logged something to look through"
    for value in secrets_:
        assert value not in logged
    audit = (await dbsession.execute(select(AuditEvent))).scalars().all()
    trail = json.dumps([[e.action, e.target_id, e.metadata_json] for e in audit])
    assert "agent_pairing.start" in trail and "agent_token.issue" in trail
    for value in secrets_:
        assert value not in trail

    # Stored: the pairing code's SHA-256 and the user code's HMAC under a key derived from the secret.
    row = await _pairing_row(dbsession, started)
    key = hmac.new(secret.encode(), b"fadenstack agent pairing: user code v1", hashlib.sha256).digest()
    assert row.pairing_code_hash == hashlib.sha256(started["pairing_code"].encode()).hexdigest()
    assert row.user_code_hmac == hmac.new(key, plain.encode(), hashlib.sha256).hexdigest()
    assert row.user_code_hmac != hashlib.sha256(plain.encode()).hexdigest()
    stored = json.dumps(
        [list(r) for r in (await dbsession.execute(sa.text("SELECT * FROM agent_pairings"))).all()], default=str,
    )
    for value in secrets_:
        assert value not in stored


# -- revoke -----------------------------------------------------------------------------------


async def test_the_app_revokes_its_own_sign_in(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    anna = await _user(dbsession)
    await _agent(client, "desk", anna)
    signed_in.user = anna
    first = (await client.post(BASE, json={"agent": "desk", "device_label": "PC"})).json()
    second = (await client.post(f"{BASE}/refresh", json={"refresh_token": first["refresh_token"]})).json()
    signed_in.user = None  # the app holds only its tokens

    # The replaced token still names the sign-in: the whole family ends.
    r = await client.post(f"{BASE}/revoke", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == status.HTTP_204_NO_CONTENT and r.headers["cache-control"] == "no-store"
    r = await client.post(f"{BASE}/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    # Always 204: again, and for a token that never was.
    for token in (second["refresh_token"], "not-a-token"):
        r = await client.post(f"{BASE}/revoke", json={"refresh_token": token})
        assert r.status_code == status.HTTP_204_NO_CONTENT
    [revoke] = await _events(dbsession, "agent_token.revoke")
    assert revoke.actor_id == anna.id
    assert json.loads(revoke.metadata_json or "{}") == {"device": first["device_id"], "by": "app"}


# -- the migration ----------------------------------------------------------------------------


async def test_the_migration_upgrades_and_downgrades(dbsession: AsyncSession) -> None:
    """Down and up again on the test database (made from the models), inside the test's transaction."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from faden_backend.db.meta import meta

    spec = importlib.util.spec_from_file_location("p41r1ng", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("p41r1ng", "tk3nus3")
    ours = {"agent_pairings", "agent_pairing_failures"}

    def run(sync_conn: Any) -> None:
        context = MigrationContext.configure(sync_conn)
        with Operations.context(context):
            migration.downgrade()
            assert not ours & set(sa.inspect(sync_conn).get_table_names())
            enum = sync_conn.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'agent_pairing_status'"))
            assert enum.first() is None

            migration.upgrade()
            inspector = sa.inspect(sync_conn)
            assert ours <= set(inspector.get_table_names())
            indexes = {i["name"]: i for i in inspector.get_indexes("agent_pairings")}
            pending = indexes["uq_agent_pairings_pending_user_code"]
            assert pending["unique"] and "pending" in str(pending.get("dialect_options", {}))
            # What the migration made is what the models describe.
            diffs = [d for d in compare_metadata(context, meta) if any(t in repr(d) for t in ours)]
            assert diffs == []

            migration.downgrade()
            migration.upgrade()

    connection = await dbsession.connection()
    await connection.run_sync(run)
