"""The second sign-in for Bypass, and the decisions of 2026-11-09 in the gateway (sdk/41; sdk/40 A3.5).

Without a database: the proof is good only for its caller and while fresh, and is never a sign-in. On Postgres
(``FADEN_TEST_GATEWAY_DSN``): no Bypass grant without a proof, the RFC 9470 challenge, the record of how the
person signed in, one proof for parallel chats, a paused Bypass resuming only while it is still allowed, and the
reason and the result summary as they are stored.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ApprovalGrant
from faden_gateway.services import extensions
from faden_gateway.services.gateway import approval_settings, grants, step_up
from faden_gateway.services.gateway.auth import verify_token
from faden_gateway.services.gateway.errors import GatewayError
from tests.test_agent_routes import (  # noqa: F401 - backend: the fixture
    AGENT_ID,
    OTHER_AGENT_ID,
    TEST_JWT_SECRET,
    USER,
    _Backend,
    _profile,
    backend,
)
from tests.test_bypass import OFF, ON, SUPPORTS, _bypass, _profile_of, _proof, _stepped_up
from tests.test_grants import (  # noqa: F401 - db_session, engine: the fixtures
    GRANTS,
    _auth,
    db_session,
    engine,
    needs_postgres,
)

CALLER = {"tenant_id": "tenant-a", "user_id": USER, "agent_id": AGENT_ID, "device_id": "device-7"}


@pytest.fixture(autouse=True)
def _settings() -> Iterator[None]:
    approval_settings.remember(ON, ttl_sec=3600)
    yield
    approval_settings.forget()


# -- the proof, without a database -------------------------------------------------------------


def test_a_fresh_proof_for_this_caller_says_how_and_when_they_signed_in() -> None:
    proof = step_up.verify(_proof(age_s=60, method="oidc", prv="entra"), **CALLER)
    assert (proof.method, proof.provider, proof.max_minutes) == ("oidc", "entra", 120)
    assert abs(proof.signed_in_at.timestamp() - (time.time() - 60)) < 3


@pytest.mark.parametrize(("token", "reason"), [
    (None, "missing"),
    ("", "missing"),
    (_proof(age_s=6 * 60), "stale"),
    (_proof(age_s=301), "stale"),
    (_proof(age_s=6 * 60, exp=int(time.time()) + 60), "stale"),
    (_proof(age_s=-120), "stale"),
    (_proof(usr="someone-else"), "mismatch"),
    (_proof(did="device-8"), "mismatch"),
    (_proof(agt=OTHER_AGENT_ID), "mismatch"),
    (_proof(ten="tenant-b"), "mismatch"),
    (_proof(typ="agent"), "invalid"),
    (_proof(aud="faden-gateway"), "invalid"),
    (_proof(method="magic"), "invalid"),
    (_proof(secret="another-secret"), "invalid"),
    (pyjwt.encode({"typ": "bypass_step_up", "aud": step_up.AUDIENCE}, TEST_JWT_SECRET, algorithm="HS256"), "invalid"),
], ids=[
    "none", "empty", "six-minutes-old", "just-past-five-minutes", "old-sign-in-with-a-later-exp", "from-the-future", "another-person",
    "another-device", "another-agent", "another-tenant", "another-type", "another-audience", "unknown-method",
    "another-key", "signed-with-the-plain-secret",
])
def test_a_proof_that_is_missing_old_or_someone_elses_is_a_challenge(token: str | None, reason: str) -> None:
    """ASVS 5.0 7.5.3: no further authentication, no highly sensitive operation; 16.5.3: nothing fails open."""
    with pytest.raises(step_up.StepUpRequiredError) as refused:
        step_up.verify(token, **CALLER)
    assert refused.value.reason == reason
    assert (refused.value.status_code, refused.value.code) == (401, "bypass_step_up_required")
    challenge = refused.value.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge and 'max_age="300"' in challenge, "RFC 9470"


def test_a_proof_is_never_a_sign_in_and_no_sign_in_is_a_proof() -> None:
    proof = _proof()
    with pytest.raises(GatewayError):
        verify_token(proof)  # another key
    plain = pyjwt.encode(
        {"typ": "bypass_step_up", "sub": USER, "exp": int(time.time()) + 60}, TEST_JWT_SECRET, algorithm="HS256",
    )
    with pytest.raises(GatewayError):
        verify_token(plain)  # its type, even under the sign-ins' key
    sign_in = pyjwt.encode({"sub": USER, "exp": int(time.time()) + 60}, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(step_up.StepUpRequiredError):
        step_up.verify(sign_in, **CALLER)
    evidence = pyjwt.encode(
        {"typ": "grant_evidence", "sub": USER, "tenant": "tenant-a", "exp": int(time.time()) + 60},
        TEST_JWT_SECRET, algorithm="HS256",
    )
    with pytest.raises(GatewayError):
        verify_token(evidence)  # the backend's grant to read evidence is no sign-in either (found in A3.5)


def test_the_result_summary_keeps_what_the_setting_says_and_an_error_always() -> None:
    assert grants.summary("x" * 3000, 2000) == "x" * 2000
    assert grants.summary("Saved.", 0) is None, "0: the outcome only"
    assert grants.summary("Error: in use " + "x" * 900, 0, error=True) == ("Error: in use " + "x" * 900)[:500]
    assert approval_settings.from_values({"result_summary_chars": 0}).result_summary_chars == 0
    assert approval_settings.from_values({"result_summary_chars": 99_999}).result_summary_chars == 2000
    assert approval_settings.from_values({}).result_summary_chars == 500


# -- the grant, end to end on Postgres ------------------------------------------------------------


def _grant_rows(session: AsyncSession) -> Any:
    return session.execute(select(ApprovalGrant).where(ApprovalGrant.kind == "bypass"))


@pytest.mark.anyio
@needs_postgres
@pytest.mark.parametrize(("proof", "reason"), [
    ({}, "missing"),
    (_stepped_up(age_s=6 * 60), "stale"),
    (_stepped_up(usr="someone-else"), "mismatch"),
    (_stepped_up(did="device-8"), "mismatch"),
    (_stepped_up(agt=OTHER_AGENT_ID), "mismatch"),
], ids=["without", "six-minutes-old", "another-persons", "another-devices", "another-agents"])
async def test_no_bypass_grant_without_a_fresh_proof_of_this_person_on_this_device_and_agent(
    proof: dict[str, str], reason: str,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    refused = await _bypass(client, proof=proof)
    assert refused.status_code == 401, refused.text
    body = refused.json()
    assert (body["error"]["code"], body["step_up"]["reason"], body["step_up"]["max_age"]) == (
        "bypass_step_up_required", reason, 300,
    )
    assert body["step_up"]["start"] == "/api/agent-tokens/step-ups"
    assert 'error="insufficient_user_authentication"' in refused.headers["www-authenticate"]
    assert (await _grant_rows(db_session)).scalars().all() == [], "no grant"


@pytest.mark.anyio
@needs_postgres
async def test_where_bypass_is_not_allowed_nobody_is_asked_to_sign_in_again(
    fastapi_app: FastAPI, client: AsyncClient,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    backend.put(_profile(max_level="auto"))
    fastapi_app.state.agent_directory.invalidate()
    refused = await _bypass(client, proof={})
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "bypass_not_allowed")


@pytest.mark.anyio
@needs_postgres
async def test_the_grant_records_how_and_when_the_person_signed_in_and_one_proof_serves_parallel_chats(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """L-59; owner, 2026-11-09: one sign-in covers switch-ons in several parallel chats for 5 minutes."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    proof_id = str(uuid.uuid4())
    proof = _stepped_up(age_s=200, method="saml", prv="adfs", jti=proof_id, max_minutes=60)
    first = await _bypass(client, proof=proof, minutes=120)
    second = await client.post(
        GRANTS, headers={**_auth(), **SUPPORTS, **proof, "x-faden-conversation-id": "conv-2"},
        json={"kind": "bypass", "minutes": 30},
    )
    assert (first.status_code, second.status_code) == (201, 201), (first.text, second.text)
    made = first.json()
    assert made["narrowed"] == ["minutes"] and 3590 <= made["remaining_s"] <= 3600, "never past what the page said"
    assert made["step_up"]["method"] == "saml" and made["step_up"]["provider"] == "adfs"
    row = (await db_session.execute(select(ApprovalGrant).where(ApprovalGrant.id == uuid.UUID(made["id"])))).scalar_one()
    assert (row.step_up_method, row.step_up_provider, str(row.step_up_id)) == ("saml", "adfs", proof_id)
    assert abs(row.step_up_at.timestamp() - (time.time() - 200)) < 5
    conversations = {g.conversation_id for g in (await _grant_rows(db_session)).scalars().all()}
    assert conversations == {"conv-1", "conv-2"}


async def _paused_bypass(client: AsyncClient) -> str:
    made = (await _bypass(client)).json()
    paused = await client.post(f"{GRANTS}/{made['id']}/pause", headers=_auth())
    assert (paused.status_code, paused.json()["status"]) == (200, "paused"), paused.text
    return str(made["id"])


@pytest.mark.anyio
@needs_postgres
async def test_a_paused_bypass_resumes_without_a_sign_in_while_it_is_still_allowed(
    fastapi_app: FastAPI, client: AsyncClient,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """Owner, 2026-11-09: the app's end pauses Bypass; resuming needs no card and no second sign-in."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    grant_id = await _paused_bypass(client)
    resumed = await client.post(f"{GRANTS}/{grant_id}/resume", headers=_auth())
    assert (resumed.status_code, resumed.json()["status"]) == (200, "active"), resumed.text


@pytest.mark.anyio
@needs_postgres
@pytest.mark.parametrize(("narrowing", "by"), [
    ("switch-off", "kill_switch"),
    ("agent-up-to-auto", "limit"),
    ("ceiling-auto", "limit"),
], ids=["installation-off", "agent-narrowed", "ee-list-narrowed"])
async def test_a_paused_bypass_that_is_no_longer_allowed_ends_instead_of_resuming(
    narrowing: str, by: str, monkeypatch: pytest.MonkeyPatch,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """ASVS 5.0 8.3.2: a narrower setting, level, ceiling or Bypass list applies at the resume."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    grant_id = await _paused_bypass(client)
    if narrowing == "switch-off":
        approval_settings.remember(OFF, ttl_sec=3600)
    elif narrowing == "agent-up-to-auto":
        backend.put(_profile(max_level="auto"))  # the same version, its highest level lowered
        fastapi_app.state.agent_directory.invalidate()
    else:
        async def ceiling(_: Any) -> str:
            return "auto"

        monkeypatch.setattr(extensions.registry, "tool_mode_ceilings", [ceiling])
    refused = await client.post(f"{GRANTS}/{grant_id}/resume", headers=_auth())
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "bypass_not_allowed"), refused.text
    row = (await db_session.execute(
        select(ApprovalGrant.status, ApprovalGrant.revoked_by).where(ApprovalGrant.id == uuid.UUID(grant_id)),
    )).one()
    assert tuple(row) == ("revoked", by)


@pytest.mark.anyio
@needs_postgres
async def test_a_paused_bypass_stays_paused_when_whether_it_is_allowed_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """ASVS 5.0 16.5.3: a ceiling that cannot be read lets nothing resume, and ends nothing for an outage."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    grant_id = await _paused_bypass(client)

    async def broken(_: Any) -> str:
        raise RuntimeError("the ceilings' database is away")

    monkeypatch.setattr(extensions.registry, "tool_mode_ceilings", [broken])
    refused = await client.post(f"{GRANTS}/{grant_id}/resume", headers=_auth())
    assert (refused.status_code, refused.json()["error"]["code"]) == (503, "grant_limits_unavailable")
    status = (await db_session.execute(
        select(ApprovalGrant.status).where(ApprovalGrant.id == uuid.UUID(grant_id)),
    )).scalar_one()
    assert status == "paused"


@pytest.mark.anyio
@needs_postgres
async def test_a_reason_of_500_characters_ending_in_an_emoji_is_stored_and_half_a_character_refused(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """The SDK cuts the reason at 500 at a character's end; a cut inside a surrogate pair is refused, not a 500."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    reason = "x" * 499 + "\U0001F600"  # 500 characters; in UTF-16 the emoji is units 499 and 500
    made = await _bypass(client, reason=reason)
    assert made.status_code == 201, made.text
    row = (await db_session.execute(
        select(ApprovalGrant).where(ApprovalGrant.id == uuid.UUID(made.json()["id"])),
    )).scalar_one()
    assert row.reason == reason, "stored and read back whole (encrypted at rest)"
    half = await client.post(
        GRANTS, headers={**_auth(), **SUPPORTS, **_stepped_up(), "content-type": "application/json"},
        content=b'{"kind": "bypass", "minutes": 30, "reason": "' + b"x" * 499 + b'\\ud83d"}',
    )
    assert half.status_code == 422, half.text
    count = (await db_session.execute(text("SELECT count(*) FROM approval_grant"))).scalar_one()
    assert count == 1


@pytest.mark.anyio
@needs_postgres
async def test_bypass_is_offered_only_to_clients_that_can_do_the_step_up(
    fastapi_app: FastAPI, client: AsyncClient,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """Additive (sdk/40 §3): a client of A3 that cannot sign the person in again never sees Bypass allowed."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    older = await _profile_of(client, **{"x-faden-client-supports": "tool_mode.bypass"})
    assert (older["tool_modes"]["max"], older["approvals"]["bypass"]["allowed"]) == ("auto", False)
    newer = await _profile_of(client, **SUPPORTS)
    assert (newer["tool_modes"]["max"], newer["approvals"]["bypass"]["allowed"]) == ("bypass", True)
    assert newer["approvals"]["bypass"]["step_up"] == {
        "required": True, "max_age": 300, "start": "/api/agent-tokens/step-ups",
    }
