"""Linked sign-ins (services/sign_in/linked.py, web/api/admin/users/sign_ins.py, the link routes of auth_sso):
seeing and unlinking one's own and, for administrators, anyone's; never the last way in; linking a provider
while signed in, to that account only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import User
from faden_backend.services import password_reset_links, provisioning
from faden_backend.services.extensions import registry
from faden_backend.services.sign_in import accounts
from faden_backend.services.sign_in.contract import ExternalIdentity
from tests.test_account_hardening import _person
from tests.test_api_tokens import _As, signed_in  # noqa: F401 - fixture used by name
from tests.test_sign_in import FakeModule, _identity, fake  # noqa: F401 - fixture used by name

pytestmark = pytest.mark.anyio


async def _linked(db: AsyncSession, user: User, provider: str = "corp", subject: str | None = None) -> OAuthAccount:
    identity = ExternalIdentity.model_validate(
        _identity(provider=provider, subject=subject or f"s-{uuid.uuid4().hex[:8]}", email=user.email),
    )
    return await accounts.link(db, user, identity)


async def _made_by_a_provider(db: AsyncSession) -> User:
    """An account a sign-in made: nobody knows its password."""
    return await provisioning.create_account(db, f"m-{uuid.uuid4().hex[:8]}@corp.example")


# ── Seeing and unlinking ────────────────────────────────────────────────


async def test_one_sees_ones_own_linked_sign_ins(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, fake: FakeModule,  # noqa: F811
) -> None:
    person = await _person(dbsession)
    link = await _linked(dbsession, person)
    await _linked(dbsession, await _person(dbsession))  # someone else's
    signed_in.user = person
    seen = (await client.get("/api/admin/users/me/sign-ins")).json()
    assert seen["password"] is True
    assert [(entry["id"], entry["provider"], entry["provider_name"]) for entry in seen["links"]] == [
        (str(link.id), "corp", "Corp"),
    ]


async def test_never_the_last_way_in(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession,  # noqa: F811
) -> None:
    made = await _made_by_a_provider(dbsession)
    assert made.password_set is False, "a provider's account has no password anyone knows"
    first, second = await _linked(dbsession, made), await _linked(dbsession, made, provider="okta")
    signed_in.user = made
    assert (await client.get("/api/admin/users/me/sign-ins")).json()["password"] is False
    assert (await client.delete(f"/api/admin/users/me/sign-ins/{second.id}")).status_code == 204
    last = await client.delete(f"/api/admin/users/me/sign-ins/{first.id}")
    assert last.status_code == 409 and last.json()["detail"] == "last_way"
    assert await dbsession.get(OAuthAccount, first.id) is not None


async def test_a_password_set_by_a_reset_link_is_a_way_in(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession,  # noqa: F811
) -> None:
    made = await _made_by_a_provider(dbsession)
    link = await _linked(dbsession, made)
    admin = await _person(dbsession, admin=True)
    token, _ = await password_reset_links.create(dbsession, made, admin, valid_hours=24, reason="test")
    await password_reset_links.use(dbsession, token, "a-long-new-password-17")
    assert made.password_set is True
    signed_in.user = made
    assert (await client.delete(f"/api/admin/users/me/sign-ins/{link.id}")).status_code == 204


async def test_a_password_a_check_refuses_is_no_way_in(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """An SSO-only domain (a password check) leaves the provider as the only way; a failing check too."""
    person = await _person(dbsession)
    link = await _linked(dbsession, person)

    async def provider_only(email: str) -> str | None:
        return "signs in with its provider"

    monkeypatch.setattr(registry, "password_sign_in_checks", [provider_only])
    signed_in.user = person
    assert (await client.get("/api/admin/users/me/sign-ins")).json()["password"] is False
    assert (await client.delete(f"/api/admin/users/me/sign-ins/{link.id}")).status_code == 409

    async def broken(email: str) -> str | None:
        raise RuntimeError("down")

    monkeypatch.setattr(registry, "password_sign_in_checks", [broken])
    assert (await client.delete(f"/api/admin/users/me/sign-ins/{link.id}")).status_code == 409


async def test_one_unlinks_only_ones_own(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession,  # noqa: F811
) -> None:
    other = await _linked(dbsession, await _person(dbsession))
    signed_in.user = await _person(dbsession)
    assert (await client.delete(f"/api/admin/users/me/sign-ins/{other.id}")).status_code == 404
    assert await dbsession.get(OAuthAccount, other.id) is not None


async def test_administrators_see_and_unlink_anyones_audited(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession,  # noqa: F811
) -> None:
    person = await _person(dbsession)
    link = await _linked(dbsession, person)
    signed_in.user = await _person(dbsession)
    assert (await client.get(f"/api/admin/users/{person.id}/sign-ins")).status_code == 403
    admin = await _person(dbsession, admin=True)
    signed_in.user = admin
    seen = (await client.get(f"/api/admin/users/{person.id}/sign-ins")).json()
    assert [entry["id"] for entry in seen["links"]] == [str(link.id)]
    assert (await client.delete(f"/api/admin/users/{person.id}/sign-ins/{link.id}")).status_code == 204
    trail = (await dbsession.execute(select(AuditEvent).where(
        AuditEvent.action == "auth.identity.unlink", AuditEvent.target_id == str(person.id),
    ))).scalar_one()
    assert trail.actor_id == admin.id and '"by_owner": false' in trail.metadata_json


# ── Linking while signed in ─────────────────────────────────────────────


async def _link(client: AsyncClient, idp: FakeModule, identity: dict[str, Any], **before: Any) -> httpx.Response:
    """Link corp from My Profile: start, sign in at the provider (*identity*), come back."""
    started = await client.post("/api/auth/sso/corp/link-start")
    assert started.status_code == 200, started.text
    transaction = started.json()["redirect_url"].split("state=")[1]
    binding = started.cookies.get("faden_sso")
    assert binding
    for work in before.values():
        await work()
    idp.identity = identity
    return await client.get(
        f"/api/auth/sso/corp/callback?code=abc&state={transaction}", cookies={"faden_sso": binding},
        follow_redirects=False,
    )


async def test_a_provider_is_linked_to_the_signed_in_account_and_nobody_signs_in(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, fake: FakeModule,  # noqa: F811
) -> None:
    person = await _person(dbsession)
    signed_in.user = person
    done = await _link(client, fake, _identity(subject="corp-7", email="elsewhere@corp.example"))
    assert done.status_code == 302 and done.headers["location"] == "/admin/profile?linked=corp"
    assert not done.cookies.get("fapiauth"), "a link signs nobody in"
    found = await accounts.find_link(dbsession, "corp", "corp-7")
    assert found is not None and found.user_id == person.id
    assert person.email != "elsewhere@corp.example", "the account keeps its own address"


async def test_an_identity_someone_else_has_stays_theirs(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, fake: FakeModule,  # noqa: F811
) -> None:
    owner = await _person(dbsession)
    await _linked(dbsession, owner, subject="taken-1")
    kept = await provisioning.put_person(
        dbsession, provisioning.source_of("corp"),
        provisioning.Person(external_id="oid-9", login="kept@corp.example", email="kept@corp.example"),
    )
    signed_in.user = await _person(dbsession)
    linked_elsewhere = await _link(client, fake, _identity(subject="taken-1"))
    assert linked_elsewhere.headers["location"] == "/admin/profile?link_refused=already_linked"
    provisioned_elsewhere = await _link(client, fake, _identity(subject="oid-9"))
    assert provisioned_elsewhere.headers["location"] == "/admin/profile?link_refused=already_linked"
    by_address = await _link(client, fake, _identity(subject="okta-9", email=kept[0].user.email))
    assert by_address.headers["location"] == "/admin/profile?link_refused=already_linked"


async def test_a_link_begun_before_the_sign_ins_ended_is_void(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, fake: FakeModule,  # noqa: F811
) -> None:
    person = await _person(dbsession)
    signed_in.user = person

    async def ended() -> None:  # a password change, a suspension: whoever began it may not finish
        person.sessions_valid_after = datetime.now(UTC) + timedelta(seconds=2)
        await dbsession.flush()

    done = await _link(client, fake, _identity(subject="late-1"), ended=ended)
    assert done.headers["location"] == "/admin/profile?link_refused=sign_in_expired"
    assert await accounts.find_link(dbsession, "corp", "late-1") is None


async def test_a_provider_s_refusal_comes_back_to_the_profile(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, fake: FakeModule,  # noqa: F811
) -> None:
    signed_in.user = await _person(dbsession)
    fake.refusal = "not_allowed"
    done = await _link(client, fake, _identity())
    assert done.headers["location"] == "/admin/profile?link_refused=not_allowed"


async def test_provisioned_only_links_only_the_provisioned(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, fake: FakeModule,  # noqa: F811
) -> None:
    signed_in.user = await _person(dbsession)
    done = await _link(client, fake, _identity(provisioned_only=True))
    assert done.headers["location"] == "/admin/profile?link_refused=not_provisioned"


async def test_a_directory_is_linked_with_its_password(
    client: AsyncClient, signed_in: _As, dbsession: AsyncSession, fake: FakeModule,  # noqa: F811
) -> None:
    person = await _person(dbsession)
    signed_in.user = person
    fake.identity = _identity(provider="dir", subject="CN=ada")
    wrong = await client.post("/api/auth/sso/dir/link-password", json={"username": "ada", "password": "wrong"})
    assert wrong.status_code == 400 and wrong.json()["detail"] == "wrong_credentials"
    right = await client.post("/api/auth/sso/dir/link-password", json={"username": "ada", "password": "right"})
    assert right.status_code == 204, right.text
    found = await accounts.find_link(dbsession, "dir", "CN=ada")
    assert found is not None and found.user_id == person.id
    redirect = await client.post("/api/auth/sso/corp/link-password", json={"username": "a", "password": "b"})
    assert redirect.status_code == 404, "a provider people are sent to is linked through link-start"
