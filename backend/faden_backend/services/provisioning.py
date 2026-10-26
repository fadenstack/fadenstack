"""Accounts and teams from outside: core's rules, in one place, for every source that changes them.

A source is named ``provider:<key>`` (``source_of``): today a sign-in provider's people, through its sign-ins,
the hourly upkeep, or Enterprise's SCIM connection for it. Each of them changes accounts only through here, so
the rules hold whoever asks:

- **Superusers are never created, changed, suspended or taken over by a source**: the local administrator must
  always get in (BSI ORP.4.A20).
- **Nothing is deleted.** A person a source removes or deactivates is suspended: the flag off, every sign-in
  ended, API tokens and agents refused (``suspend``). A source lifts only a suspension it made; an
  administrator's stays (``ProvisionedAccount.suspended_by_source``).
- **As little as works is kept** (GDPR Art. 5(1)(c)): of a person, the address, the source's ID for
  them, their login name there and whether they are active (``Person``); of a team, its name and the
  source's ID for it.
  Everything else stays with the source.
- **A person is found again by the source's ID**, never by address alone. An account that already has the
  address is taken over only when it is no superuser's and no other source's (round 3, decision 4).
- **Teams a source keeps** are named by it and carry no roles; their memberships are marked with the
  source, and memberships an administrator gave are never touched. A name an administrator's team has
  is refused.
- **Every change is audited**, with the source as who did it.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.provisioning import ProvisionedAccount
from faden_backend.db.models.rbac import Role
from faden_backend.db.models.users import User, end_sign_ins

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

RefusalCode = Literal["conflict", "superuser", "not_found", "invalid"]


class Refused(Exception):  # noqa: N818 -- a refusal, said to the source
    """What a source asked for, and why it is not done (``code``: SCIM's error types map onto it)."""

    def __init__(self, code: RefusalCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Person:
    """What a source says about a person: all core keeps of it."""

    external_id: str
    login: str
    email: str
    active: bool = True


@dataclass(frozen=True)
class Provisioned:
    """A person a source keeps: their account and how the source names them."""

    user: User
    record: ProvisionedAccount


def source_of(provider: str) -> str:
    """How what sign-in provider *provider* keeps is marked: memberships, teams, provisioned accounts."""
    return f"provider:{provider}"


async def _audit(
    session: AsyncSession,
    action: str,
    target_type: str,
    target_id: str,
    *,
    actor_id: uuid.UUID | None = None,
    severity: str = "normal",
    **metadata: object,
) -> None:
    await AuditDAO(session).log(
        action=action, target_type=target_type, target_id=target_id, result=AuditResult.ALLOW,
        actor_id=actor_id, severity=severity, source=None if actor_id else "system",
        metadata_json=json.dumps(metadata, default=str),
    )


# ── Accounts ────────────────────────────────────────────────────────────────────────────────────────────


async def create_account(
    session: AsyncSession, email: str, *, role_ids: Iterable[uuid.UUID] = (), **audit: object,
) -> User:
    """A new account for *email*: verified, no usable password, the given roles (else ``default_user``).

    Never a superuser. *audit* is recorded with ``user.create``.
    """
    user = User(
        id=uuid.uuid4(), email=email, hashed_password=PasswordHelper().hash(secrets.token_urlsafe(32)),
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(user)
    await session.flush()
    rbac = RbacDAO(session)
    wanted = list(role_ids)
    found = list(await session.scalars(select(Role.id).where(Role.id.in_(wanted)))) if wanted else []
    if found:
        await rbac.set_user_roles(user.id, found)
    else:
        default = await rbac.get_role_by_name("default_user")
        if default is not None:
            await rbac.assign_role(user.id, default.id)
    await session.flush()
    await _audit(session, "user.create", "user", str(user.id), severity="high", email=email, **audit)
    return user


async def suspend(
    session: AsyncSession,
    user: User,
    *,
    reason: str,
    actor_id: uuid.UUID | None = None,
    source: str | None = None,
    by: str | None = None,
    **audit: object,
) -> bool:
    """Suspend *user* (the flag off, every sign-in ended); whether that changed anything.

    *actor_id*: the administrator who does it. *source*: the source whose suspension it is, so that source may
    lift it again (the account's provisioning record remembers). *by*: who did it, for the audit trail, when
    neither says (e.g. the inactivity upkeep, whose suspension no source may lift). Without an administrator,
    a superuser is never suspended.
    """
    if actor_id is None and user.is_superuser:
        log.warning("%s would suspend superuser %s; left to an administrator", by or source, user.id)
        return False
    if not user.is_active:
        return False
    user.is_active = False
    end_sign_ins(user)
    record = await session.get(ProvisionedAccount, user.id)
    if record is not None:
        record.suspended_by_source = source is not None and record.source == source
    await session.flush()
    await _audit(
        session, "user.suspend", "user", str(user.id), actor_id=actor_id, severity="high",
        reason=reason, by=by or source or "administrator", email=user.email, **audit,
    )
    return True


async def reactivate(
    session: AsyncSession, user: User, *, actor_id: uuid.UUID | None = None, source: str | None = None,
) -> bool:
    """Let *user* back in; whether that changed anything. A source lifts only a suspension it made."""
    if user.is_active:
        return False
    record = await session.get(ProvisionedAccount, user.id)
    if source is not None:
        its_own = record is not None and record.source == source and record.suspended_by_source
        if not its_own:
            return False
    user.is_active = True
    if record is not None:
        record.suspended_by_source = False
    await session.flush()
    await _audit(
        session, "user.reactivate", "user", str(user.id), actor_id=actor_id, severity="high",
        by=source or "administrator", email=user.email,
    )
    return True


# ── People a source keeps ───────────────────────────────────────────────────────────────────────────────


async def find_person(
    session: AsyncSession,
    source: str,
    *,
    user_id: uuid.UUID | None = None,
    external_id: str | None = None,
    login: str | None = None,
    removed: bool = False,
) -> Provisioned | None:
    """The person *source* keeps with this account, ID or login name, if any.

    With *removed*, also one the source removed.
    """
    query = select(ProvisionedAccount, User).join(User, User.id == ProvisionedAccount.user_id).where(
        ProvisionedAccount.source == source,
    )
    if not removed:
        query = query.where(ProvisionedAccount.removed_at.is_(None))
    if user_id is not None:
        query = query.where(ProvisionedAccount.user_id == user_id)
    if external_id is not None:
        query = query.where(ProvisionedAccount.external_id == external_id)
    if login is not None:
        query = query.where(func.lower(ProvisionedAccount.login) == login.lower())
    row = (await session.execute(query.limit(1))).first()
    return Provisioned(user=row[1], record=row[0]) if row else None


async def list_people(
    session: AsyncSession,
    source: str,
    *,
    external_id: str | None = None,
    login: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[int, list[Provisioned]]:
    """How many people *source* keeps (matching an ID or login name), and a page of them, oldest first."""
    where = [ProvisionedAccount.source == source, ProvisionedAccount.removed_at.is_(None)]
    if external_id is not None:
        where.append(ProvisionedAccount.external_id == external_id)
    if login is not None:
        where.append(func.lower(ProvisionedAccount.login) == login.lower())
    total = await session.scalar(select(func.count()).select_from(ProvisionedAccount).where(*where)) or 0
    rows = (await session.execute(
        select(ProvisionedAccount, User).join(User, User.id == ProvisionedAccount.user_id).where(*where)
        .order_by(ProvisionedAccount.provisioned_at, ProvisionedAccount.user_id)
        .offset(max(offset, 0)).limit(max(min(limit, 1000), 0)),
    )).all()
    return total, [Provisioned(user=user, record=record) for record, user in rows]


async def _taken(session: AsyncSession, source: str, person: Person, user_id: uuid.UUID | None) -> None:
    """Refuse an ID or login name another of *source*'s people has."""
    for column, value, what in (
        (ProvisionedAccount.external_id, person.external_id, "ID"),
        (func.lower(ProvisionedAccount.login), person.login.lower(), "login name"),
    ):
        other = await session.scalar(select(ProvisionedAccount.user_id).where(
            ProvisionedAccount.source == source, column == value,
        ))
        if other is not None and other != user_id:
            raise Refused("conflict", f"Another person of this source has that {what}.")


def _check(person: Person) -> None:
    for value, what, longest in ((person.external_id, "ID", 255), (person.login, "login name", 320),
                                 (person.email, "address", 320)):
        if not value or not value.strip() or len(value) > longest:
            raise Refused("invalid", f"The {what} is missing or longer than {longest} characters.")
    if "@" not in person.email:
        raise Refused("invalid", "The address is not an e-mail address.")


async def put_person(
    session: AsyncSession,
    source: str,
    person: Person,
    *,
    user_id: uuid.UUID | None = None,
    role_ids: Iterable[uuid.UUID] = (),
) -> tuple[Provisioned, bool]:
    """Create (*user_id* ``None``) or update a person *source* keeps; the person and whether it is new to it.

    New: a person the source removed comes back with their account; else an account with the address is
    taken over when it is no superuser's and no other source's; else a new account with *role_ids*.
    Update: the address, ID and login name follow the source's; ``active`` suspends or lifts the source's
    own suspension.
    """
    _check(person)
    if user_id is None:
        back = await find_person(session, source, external_id=person.external_id, removed=True)
        if back is not None and back.record.removed_at is not None:
            await _taken(session, source, person, back.user.id)
            back.record.removed_at = None
            await _audit(session, "user.provision", "user", str(back.user.id), by=source, how="back",
                         email=back.user.email)
            await _update(session, source, back, person)
            return back, True
    await _taken(session, source, person, user_id)
    if user_id is not None:
        found = await find_person(session, source, user_id=user_id)
        if found is None:
            raise Refused("not_found", "This source keeps no such person.")
        await _update(session, source, found, person)
        return found, False

    existing = await session.scalar(select(User).where(func.lower(User.email) == person.email.lower()))
    if existing is not None:
        if existing.is_superuser:
            raise Refused(
                "superuser", "An administrator's account has this address; a source never takes it over.",
            )
        other = await session.get(ProvisionedAccount, existing.id)
        if other is not None:
            raise Refused("conflict", "Another source keeps the account with this address.")
        user, how = existing, "taken_over"
    else:
        user = await create_account(session, person.email, role_ids=role_ids, via=source)
        how = "created"
    record = ProvisionedAccount(
        user_id=user.id, source=source, external_id=person.external_id, login=person.login,
        suspended_by_source=False,
    )
    session.add(record)
    await session.flush()
    await _audit(
        session, "user.provision", "user", str(user.id), severity="high" if how == "taken_over" else "normal",
        by=source, how=how, email=user.email,
    )
    if not person.active:
        await suspend(session, user, reason="deactivated_by_source", source=source)
    # A taken-over account an administrator suspended stays suspended: a source lifts only its own.
    return Provisioned(user=user, record=record), True


async def _update(session: AsyncSession, source: str, found: Provisioned, person: Person) -> None:
    user, record = found.user, found.record
    if user.is_superuser:
        raise Refused("superuser", "An administrator's account is not changed by a source.")
    changed: list[str] = []
    if person.email.lower() != user.email.lower():
        clash = await session.scalar(
            select(User.id).where(func.lower(User.email) == person.email.lower(), User.id != user.id),
        )
        if clash is not None:
            raise Refused("conflict", "Another account has this address.")
        user.email = person.email
        end_sign_ins(user)
        changed.append("email")
    if person.external_id != record.external_id:
        record.external_id = person.external_id
        changed.append("external_id")
    if person.login != record.login:
        record.login = person.login
        changed.append("login")
    await session.flush()
    if changed:
        await _audit(session, "user.provision.update", "user", str(user.id), by=source, changed=changed,
                     email=user.email)
    if person.active:
        await reactivate(session, user, source=source)
    else:
        await suspend(session, user, reason="deactivated_by_source", source=source)


async def remove_person(session: AsyncSession, source: str, user_id: uuid.UUID) -> None:
    """The source no longer has the person: suspended and hidden from it, never deleted.

    The source adding the same ID again brings the same account back (``put_person``).
    """
    found = await find_person(session, source, user_id=user_id)
    if found is None:
        raise Refused("not_found", "This source keeps no such person.")
    if found.user.is_superuser:
        raise Refused("superuser", "An administrator's account is not changed by a source.")
    await suspend(session, found.user, reason="removed_by_source", source=source)
    found.record.removed_at = datetime.now(UTC)
    await session.flush()
    await _audit(session, "user.provision.remove", "user", str(user_id), by=source, email=found.user.email)


# ── Teams a source keeps ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Team:
    """What a source says about a team: its name and its own ID for it."""

    name: str
    external_id: str | None = None


async def find_team(
    session: AsyncSession,
    source: str,
    *,
    group_id: uuid.UUID | None = None,
    name: str | None = None,
    external_id: str | None = None,
) -> Group | None:
    """The team *source* keeps with this ID, name or source ID, if any."""
    query = select(Group).where(Group.source == source)
    if group_id is not None:
        query = query.where(Group.id == group_id)
    if name is not None:
        query = query.where(Group.name == name)
    if external_id is not None:
        query = query.where(Group.external_id == external_id)
    return await session.scalar(query.limit(1))


async def list_teams(
    session: AsyncSession,
    source: str,
    *,
    name: str | None = None,
    external_id: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[int, list[Group]]:
    """How many teams *source* keeps (matching a name or source ID), and a page of them, by name."""
    where = [Group.source == source]
    if name is not None:
        where.append(Group.name == name)
    if external_id is not None:
        where.append(Group.external_id == external_id)
    total = await session.scalar(select(func.count()).select_from(Group).where(*where)) or 0
    groups = await session.scalars(
        select(Group).where(*where).order_by(Group.name)
        .offset(max(offset, 0)).limit(max(min(limit, 1000), 0)),
    )
    return total, list(groups)


async def put_team(
    session: AsyncSession, source: str, team: Team, *, group_id: uuid.UUID | None = None,
) -> tuple[Group, bool]:
    """Create (*group_id* ``None``) or rename a team *source* keeps; the team and whether it is new.

    A team *source* kept before under this name is taken up again; a name another team has is refused.
    """
    name = (team.name or "").strip()
    if not name or len(name) > 128:
        raise Refused("invalid", "A team's name is missing or longer than 128 characters.")
    holder = await session.scalar(select(Group).where(Group.name == name))
    if group_id is None and holder is not None and holder.source == source:
        group_id = holder.id
    if holder is not None and holder.id != group_id:
        raise Refused("conflict", "Another team has this name.")
    if group_id is not None:
        group = await find_team(session, source, group_id=group_id)
        if group is None:
            raise Refused("not_found", "This source keeps no such team.")
        changed = [what for what, old, new in (("name", group.name, name),
                                               ("external_id", group.external_id, team.external_id))
                   if new is not None and old != new]
        group.name = name
        if team.external_id is not None:
            group.external_id = team.external_id
        await session.flush()
        if changed:
            await _audit(session, "team.provision.update", "group", str(group.id), by=source, changed=changed,
                         name=name)
        return group, False
    group = Group(id=uuid.uuid4(), name=name, source=source, external_id=team.external_id)
    session.add(group)
    await session.flush()
    await _audit(session, "team.provision", "group", str(group.id), by=source, name=name)
    return group, True


async def members(session: AsyncSession, source: str, group_id: uuid.UUID) -> list[uuid.UUID]:
    """The members *source* gave team *group_id*."""
    return list(await session.scalars(
        select(UserGroup.user_id).where(UserGroup.group_id == group_id, UserGroup.source == source)
        .order_by(UserGroup.user_id),
    ))


async def _people_of(session: AsyncSession, source: str, user_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """*user_ids*, refused unless every one is a person *source* keeps."""
    wanted = set(user_ids)
    if not wanted:
        return wanted
    known = set(await session.scalars(select(ProvisionedAccount.user_id).where(
        ProvisionedAccount.source == source, ProvisionedAccount.user_id.in_(wanted),
    )))
    if known != wanted:
        raise Refused("invalid", "A member is not a person this source keeps.")
    return wanted


async def change_members(
    session: AsyncSession,
    source: str,
    group_id: uuid.UUID,
    *,
    add: Sequence[uuid.UUID] = (),
    remove: Sequence[uuid.UUID] = (),
    replace: Sequence[uuid.UUID] | None = None,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Change the memberships *source* keeps in its team; those added and removed.

    *replace* makes them exactly that; else *add* and *remove*. Memberships an administrator gave stay.
    """
    group = await find_team(session, source, group_id=group_id)
    if group is None:
        raise Refused("not_found", "This source keeps no such team.")
    current = set(await members(session, source, group_id))
    if replace is not None:
        wanted = await _people_of(session, source, replace)
    else:
        wanted = (current | await _people_of(session, source, add)) - set(remove)
    added, removed = sorted(wanted - current), sorted(current - wanted)
    if added:
        await session.execute(
            pg_insert(UserGroup)
            .values([{"user_id": u, "group_id": group_id, "source": source} for u in added])
            .on_conflict_do_nothing(),
        )
    if removed:
        await session.execute(delete(UserGroup).where(
            UserGroup.group_id == group_id, UserGroup.source == source, UserGroup.user_id.in_(removed),
        ))
    await session.flush()
    if added or removed:
        await _audit(session, "team.members.provision", "group", str(group_id), by=source,
                     added=[str(u) for u in added], removed=[str(u) for u in removed])
    return added, removed


async def remove_team(session: AsyncSession, source: str, group_id: uuid.UUID) -> None:
    """The source no longer has the team: the memberships it gave end, and the team is an administrator's.

    The team, its grants and an administrator's memberships stay, so nothing given to it is lost by accident;
    the source no longer sees it.
    """
    group = await find_team(session, source, group_id=group_id)
    if group is None:
        raise Refused("not_found", "This source keeps no such team.")
    await change_members(session, source, group_id, replace=[])
    group.source = None
    group.external_id = None
    await session.flush()
    await _audit(session, "team.provision.remove", "group", str(group_id), by=source, name=group.name)


async def sync_memberships(
    session: AsyncSession, user: User, source: str, group_ids: Iterable[uuid.UUID], *, remove: bool = True,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """The memberships *source* keeps for *user*, made to match *group_ids* (any team); added and removed.

    For a sign-in's answer and the directory upkeep. Without *remove* none are taken away. An administrator's
    memberships stay as they are.
    """
    wanted: set[uuid.UUID] = set()
    ids = list(group_ids)
    if ids:
        wanted = set(await session.scalars(select(Group.id).where(Group.id.in_(ids))))
    rows = (await session.execute(
        select(UserGroup.group_id, UserGroup.source).where(UserGroup.user_id == user.id),
    )).all()
    kept = {group_id for group_id, row_source in rows if row_source == source}
    member_of = {group_id for group_id, _ in rows}
    added = sorted(wanted - member_of)
    removed = sorted(kept - wanted) if remove else []
    if added:
        await session.execute(
            pg_insert(UserGroup)
            .values([{"user_id": user.id, "group_id": group_id, "source": source} for group_id in added])
            .on_conflict_do_nothing(),
        )
    if removed:
        await session.execute(delete(UserGroup).where(
            UserGroup.user_id == user.id, UserGroup.source == source, UserGroup.group_id.in_(removed),
        ))
    await session.flush()
    return added, removed
