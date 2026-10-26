"""Accounts and teams from outside (services/provisioning.py): core's rules, whichever source asks."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.groups import Group, GroupRole, UserGroup
from faden_backend.db.models.provisioning import ProvisionedAccount
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import provisioning
from faden_backend.services.provisioning import Person, Refused, Team

pytestmark = pytest.mark.anyio

ENTRA = provisioning.source_of("entra")
OKTA = provisioning.source_of("okta")


def _person(name: str = "ada", *, active: bool = True, **changes: str) -> Person:
    tag = uuid.uuid4().hex[:8]
    fields = {"external_id": f"oid-{name}-{tag}", "login": f"{name}-{tag}@corp.test", "email": f"{name}-{tag}@corp.test"}
    return Person(active=active, **{**fields, **changes})


async def _account(db: AsyncSession, *, admin: bool = False, active: bool = True) -> User:
    user = User(id=uuid.uuid4(), email=f"local-{uuid.uuid4().hex[:8]}@corp.test", hashed_password="x",  # noqa: S106
                is_active=active, is_verified=True, is_superuser=admin)
    db.add(user)
    await db.flush()
    return user


async def _actions(db: AsyncSession, target: uuid.UUID) -> list[str]:
    rows = await db.scalars(select(AuditEvent.action).where(AuditEvent.target_id == str(target)))
    return list(rows)


# ── People ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_new_person_gets_an_account_and_only_what_finds_them_again(dbsession: AsyncSession) -> None:
    person = _person()
    found, new = await provisioning.put_person(dbsession, ENTRA, person)
    assert new and found.user.email == person.email and found.user.is_active and not found.user.is_superuser
    assert (found.record.source, found.record.external_id, found.record.login) == (
        ENTRA, person.external_id, person.login,
    )
    assert await provisioning.find_person(dbsession, ENTRA, external_id=person.external_id) is not None
    assert await provisioning.find_person(dbsession, ENTRA, login=person.login.upper()) is not None, "any case"
    assert await provisioning.find_person(dbsession, OKTA, external_id=person.external_id) is None, "per source"
    assert {"user.create", "user.provision"} <= set(await _actions(dbsession, found.user.id))


async def test_an_account_with_the_address_is_taken_over_unless_an_administrator_s(dbsession: AsyncSession) -> None:
    local = await _account(dbsession)
    found, new = await provisioning.put_person(dbsession, ENTRA, _person(email=local.email))
    assert new and found.user.id == local.id, "decision 4: taken over, not a second account"
    event = await dbsession.scalar(select(AuditEvent).where(
        AuditEvent.action == "user.provision", AuditEvent.target_id == str(local.id),
    ))
    assert event is not None and event.severity == "high" and '"taken_over"' in event.metadata_json

    admin = await _account(dbsession, admin=True)
    with pytest.raises(Refused) as refused:
        await provisioning.put_person(dbsession, ENTRA, _person(email=admin.email))
    assert refused.value.code == "superuser"
    with pytest.raises(Refused) as other:
        await provisioning.put_person(dbsession, OKTA, _person(email=local.email))
    assert other.value.code == "conflict", "another source keeps it"


async def test_an_id_or_login_name_is_one_person_s_per_source(dbsession: AsyncSession) -> None:
    first, _ = await provisioning.put_person(dbsession, ENTRA, _person("ada"))
    for clash in (
        _person("ben", external_id=first.record.external_id),
        _person("ben", login=first.record.login.upper()),
    ):
        with pytest.raises(Refused) as refused:
            await provisioning.put_person(dbsession, ENTRA, clash)
        assert refused.value.code == "conflict"
    with pytest.raises(Refused) as invalid:
        await provisioning.put_person(dbsession, ENTRA, _person("cy", email="not-an-address"))
    assert invalid.value.code == "invalid"


async def test_an_update_follows_the_source(dbsession: AsyncSession) -> None:
    found, _ = await provisioning.put_person(dbsession, ENTRA, _person("ada"))
    moved = Person(external_id="oid-new", login="ada.new@corp.test", email="ada.new@corp.test")
    same, new = await provisioning.put_person(dbsession, ENTRA, moved, user_id=found.user.id)
    assert not new and (same.user.email, same.record.login, same.record.external_id) == (
        "ada.new@corp.test", "ada.new@corp.test", "oid-new",
    )
    other = await _account(dbsession)
    with pytest.raises(Refused) as clash:
        await provisioning.put_person(
            dbsession, ENTRA, Person(external_id="oid-new", login="x@corp.test", email=other.email),
            user_id=found.user.id,
        )
    assert clash.value.code == "conflict"
    with pytest.raises(Refused) as missing:
        await provisioning.put_person(dbsession, ENTRA, _person(), user_id=uuid.uuid4())
    assert missing.value.code == "not_found"


async def test_deactivated_is_suspended_and_the_source_lifts_only_its_own(dbsession: AsyncSession) -> None:
    person = _person("ada")
    found, _ = await provisioning.put_person(dbsession, ENTRA, person)
    user = found.user
    await provisioning.put_person(dbsession, ENTRA, Person(**{**person.__dict__, "active": False}), user_id=user.id)
    assert not user.is_active and user.sessions_valid_after is not None, "every sign-in ended"
    assert (await dbsession.get(ProvisionedAccount, user.id)).suspended_by_source
    await provisioning.put_person(dbsession, ENTRA, person, user_id=user.id)
    assert user.is_active, "its own suspension lifted"

    # An administrator's suspension stays, whatever the source says.
    assert await provisioning.suspend(dbsession, user, reason="administrator", actor_id=uuid.uuid4())
    assert not (await dbsession.get(ProvisionedAccount, user.id)).suspended_by_source
    await provisioning.put_person(dbsession, ENTRA, person, user_id=user.id)
    assert not user.is_active
    # So does the inactivity upkeep's (no source).
    assert await provisioning.reactivate(dbsession, user, actor_id=uuid.uuid4())
    assert await provisioning.suspend(dbsession, user, reason="inactive_45_days", by="sign_in_upkeep")
    assert not await provisioning.reactivate(dbsession, user, source=ENTRA)
    assert not user.is_active


async def test_removed_means_suspended_and_hidden_never_deleted(dbsession: AsyncSession) -> None:
    person = _person()
    found, _ = await provisioning.put_person(dbsession, ENTRA, person)
    await provisioning.remove_person(dbsession, ENTRA, found.user.id)
    assert await dbsession.get(User, found.user.id) is not None and not found.user.is_active
    assert await provisioning.find_person(dbsession, ENTRA, user_id=found.user.id) is None, "gone for the source"
    assert (await provisioning.list_people(dbsession, ENTRA, external_id=person.external_id))[0] == 0
    with pytest.raises(Refused):
        await provisioning.remove_person(dbsession, ENTRA, found.user.id)

    back, new = await provisioning.put_person(dbsession, ENTRA, person)
    assert new and back.user.id == found.user.id and back.user.is_active, "the same account, let back in"
    assert await provisioning.find_person(dbsession, ENTRA, external_id=person.external_id) is not None


async def test_no_source_ever_suspends_or_changes_a_superuser(dbsession: AsyncSession) -> None:
    admin = await _account(dbsession, admin=True)
    assert not await provisioning.suspend(dbsession, admin, reason="directory_missing", source=ENTRA)
    assert not await provisioning.suspend(dbsession, admin, reason="inactive", by="sign_in_upkeep")
    assert admin.is_active
    dbsession.add(ProvisionedAccount(user_id=admin.id, source=ENTRA, external_id="oid-root", login="root@corp.test"))
    await dbsession.flush()
    with pytest.raises(Refused) as refused:
        await provisioning.put_person(
            dbsession, ENTRA, Person(external_id="oid-root", login="root@corp.test", email=admin.email, active=False),
            user_id=admin.id,
        )
    assert refused.value.code == "superuser" and admin.is_active


async def test_people_are_listed_and_filtered_per_source(dbsession: AsyncSession) -> None:
    made = [(await provisioning.put_person(dbsession, OKTA, _person(n)))[0] for n in ("a", "b", "c")]
    total, page = await provisioning.list_people(dbsession, OKTA, offset=1, limit=1)
    assert total >= 3 and len(page) == 1
    total, found = await provisioning.list_people(dbsession, OKTA, login=made[1].record.login)
    assert (total, [p.user.id for p in found]) == (1, [made[1].user.id])


# ── Teams ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_team_is_made_kept_and_renamed_by_its_source(dbsession: AsyncSession) -> None:
    name = f"Finance {uuid.uuid4().hex[:6]}"
    team, new = await provisioning.put_team(dbsession, ENTRA, Team(name=name, external_id="gid-1"))
    assert new and (team.source, team.external_id) == (ENTRA, "gid-1")
    roles = await dbsession.scalar(select(func.count()).select_from(GroupRole).where(GroupRole.group_id == team.id))
    assert roles == 0, "a source's team carries no roles"
    again, new = await provisioning.put_team(dbsession, ENTRA, Team(name=name))
    assert not new and again.id == team.id, "its own team under that name is taken up again"
    assert await provisioning.find_team(dbsession, ENTRA, external_id="gid-1") is None, "an ID it no longer gives"
    renamed, _ = await provisioning.put_team(
        dbsession, ENTRA, Team(name=f"{name} EU", external_id="gid-2"), group_id=team.id,
    )
    assert (renamed.name, renamed.external_id) == (f"{name} EU", "gid-2")
    assert await provisioning.find_team(dbsession, ENTRA, external_id="gid-2") is not None

    held = Group(id=uuid.uuid4(), name=f"Admins {uuid.uuid4().hex[:6]}")
    dbsession.add(held)
    await dbsession.flush()
    with pytest.raises(Refused) as refused:
        await provisioning.put_team(dbsession, ENTRA, Team(name=held.name))
    assert refused.value.code == "conflict", "an administrator's team is not taken over"


async def test_memberships_follow_the_source_and_leave_an_administrator_s(dbsession: AsyncSession) -> None:
    team, _ = await provisioning.put_team(dbsession, ENTRA, Team(name=f"Sales {uuid.uuid4().hex[:6]}"))
    ada, _ = await provisioning.put_person(dbsession, ENTRA, _person("ada"))
    ben, _ = await provisioning.put_person(dbsession, ENTRA, _person("ben"))
    local = await _account(dbsession)
    dbsession.add(UserGroup(user_id=local.id, group_id=team.id, source=None))  # an administrator added them
    await dbsession.flush()

    added, _ = await provisioning.change_members(dbsession, ENTRA, team.id, add=[ada.user.id, ben.user.id])
    assert sorted(added) == sorted([ada.user.id, ben.user.id])
    _, removed = await provisioning.change_members(dbsession, ENTRA, team.id, remove=[ben.user.id])
    assert removed == [ben.user.id]
    await provisioning.change_members(dbsession, ENTRA, team.id, replace=[ben.user.id])
    assert await provisioning.members(dbsession, ENTRA, team.id) == [ben.user.id]
    with pytest.raises(Refused) as refused:
        await provisioning.change_members(dbsession, ENTRA, team.id, add=[local.id])
    assert refused.value.code == "invalid", "only people the source keeps"

    await provisioning.remove_team(dbsession, ENTRA, team.id)
    kept = await dbsession.get(Group, team.id)
    assert kept is not None and kept.source is None, "the team and what was given to it stay, an administrator's"
    assert await provisioning.find_team(dbsession, ENTRA, group_id=team.id) is None
    left = set(await dbsession.scalars(select(UserGroup.user_id).where(UserGroup.group_id == team.id)))
    assert left == {local.id}, "the administrator's membership stays"


async def test_the_console_sees_who_keeps_a_team(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    admin = await _account(dbsession, admin=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    team, _ = await provisioning.put_team(dbsession, ENTRA, Team(name=f"Ops {uuid.uuid4().hex[:6]}"))
    mine = Group(id=uuid.uuid4(), name=f"Mine {uuid.uuid4().hex[:6]}")
    dbsession.add(mine)
    await dbsession.flush()
    kept = await client.get(fastapi_app.url_path_for("get_group", group_id=str(team.id)))
    assert kept.status_code == 200 and kept.json()["source"] == ENTRA
    assert (await client.get(fastapi_app.url_path_for("get_group", group_id=str(mine.id)))).json()["source"] is None
