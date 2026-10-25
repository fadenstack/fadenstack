"""The hourly upkeep of accounts from outside sign-ins: inactive ones and those a directory no longer has.

CIS v8 5.3 (dormant accounts after 45 days), 6.2 (access of leavers revoked, automatically); BSI
ORP.4.A2 (rights that are no longer needed are removed), ORP.4.A20 (the administrator always gets in).
The module is faked here; ``fadenstack-auth``'s chain test runs it with a real directory.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.agents import Agent, AgentRefreshToken
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import User
from faden_backend.services.sign_in import accounts, module, upkeep
from faden_backend.services.sign_in.contract import DirectoryAnswer, DirectoryPerson, ProviderUpkeep
from faden_backend.settings import settings
from tests.test_account_hardening import _person

pytestmark = pytest.mark.anyio
NOW = datetime.now(UTC)


class FakeModule:
    """The module's upkeep answers, set per test."""

    def __init__(self) -> None:
        self.providers: list[ProviderUpkeep] = []
        self.people: dict[str, str] = {}
        self.manage_groups = False
        self.group_ids: list[uuid.UUID] = []
        self.down = False
        self.asked: list[list[str]] = []

    async def upkeep(self) -> list[ProviderUpkeep]:
        """The providers set."""
        return self.providers

    async def directory(self, key: str, subjects: list[str]) -> DirectoryAnswer:
        """Each subject's status as set; ``missing`` for the others."""
        self.asked.append(subjects)
        if self.down:
            msg = "the directory cannot be asked"
            raise module.ModuleRefusedError(503, msg)
        return DirectoryAnswer(manage_groups=self.manage_groups, people=[
            DirectoryPerson(subject=s, status=self.people.get(s, "missing"),  # type: ignore[arg-type]
                            group_ids=self.group_ids)
            for s in subjects
        ])


@pytest.fixture
def db(dbsession: AsyncSession) -> AsyncSession:
    return dbsession


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeModule:
    faked = FakeModule()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(module, "upkeep", faked.upkeep)
    monkeypatch.setattr(module, "directory", faked.directory)
    return faked


async def _linked(db: AsyncSession, *, days_ago: float | None, provider: str = "corp", admin: bool = False,
                  subject: str | None = None) -> User:
    user = await _person(db, admin=admin)
    db.add(OAuthAccount(
        id=uuid.uuid4(), user_id=user.id, oauth_name=provider, account_id=subject or uuid.uuid4().hex,
        access_token="", last_sign_in_at=None if days_ago is None else NOW - timedelta(days=days_ago),
    ))
    await db.flush()
    return user


async def _active(db: AsyncSession, user: User) -> bool:
    await db.refresh(user)
    return user.is_active


# ── Inactive ────────────────────────────────────────────────────────────


async def test_an_account_unused_for_its_providers_days_is_suspended(db: AsyncSession, fake: FakeModule) -> None:
    """CIS v8 5.3: 45 days by default; the sign-ins it had are ended too."""
    fake.providers = [ProviderUpkeep(key="corp", inactive_days=45)]
    dormant = await _linked(db, days_ago=46)
    recent = await _linked(db, days_ago=10)
    report = await upkeep.upkeep_once(db)
    assert report is not None and [user for user, _ in report.suspended] == [dormant.id]
    assert not await _active(db, dormant) and dormant.sessions_valid_after is not None
    assert await _active(db, recent)


async def test_a_superuser_is_never_suspended_for_it(db: AsyncSession, fake: FakeModule) -> None:
    """BSI ORP.4.A20: the administrator always gets in."""
    fake.providers = [ProviderUpkeep(key="corp", inactive_days=45)]
    admin = await _linked(db, days_ago=400, admin=True)
    await upkeep.upkeep_once(db)
    assert await _active(db, admin)


async def test_an_agent_in_use_keeps_the_account(db: AsyncSession, fake: FakeModule) -> None:
    """Someone who works through an agent signs in rarely; the agent renewing its sign-in counts as use."""
    fake.providers = [ProviderUpkeep(key="corp", inactive_days=45)]
    user = await _linked(db, days_ago=90)
    slug = f"agent-{uuid.uuid4().hex[:6]}"
    agent = Agent(id=uuid.uuid4(), slug=slug, name=slug, created_by=user.id)
    db.add(agent)
    await db.flush()
    db.add(AgentRefreshToken(
        family_id=uuid.uuid4(), agent_id=agent.id, user_id=user.id, token_hash=uuid.uuid4().hex,
        created_at=NOW - timedelta(days=60), last_used_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30), idle_expires_at=NOW + timedelta(days=7),
    ))
    await db.flush()
    await upkeep.upkeep_once(db)
    assert await _active(db, user)


async def test_a_sign_in_with_another_provider_counts(db: AsyncSession, fake: FakeModule) -> None:
    fake.providers = [ProviderUpkeep(key="corp", inactive_days=45)]
    user = await _linked(db, days_ago=60)
    db.add(OAuthAccount(id=uuid.uuid4(), user_id=user.id, oauth_name="other", account_id=uuid.uuid4().hex,
                        access_token="", last_sign_in_at=NOW - timedelta(days=2)))
    await db.flush()
    await upkeep.upkeep_once(db)
    assert await _active(db, user)


async def test_no_recorded_use_no_suspension(db: AsyncSession, fake: FakeModule) -> None:
    """A link from before sign-ins were recorded says nothing about use: left alone."""
    fake.providers = [ProviderUpkeep(key="corp", inactive_days=45)]
    user = await _linked(db, days_ago=None)
    await upkeep.upkeep_once(db)
    assert await _active(db, user)


async def test_without_a_limit_nobody_is_suspended(db: AsyncSession, fake: FakeModule) -> None:
    fake.providers = [ProviderUpkeep(key="corp", inactive_days=None)]
    user = await _linked(db, days_ago=4000)
    await upkeep.upkeep_once(db)
    assert await _active(db, user)


async def test_other_providers_people_are_not_its_business(db: AsyncSession, fake: FakeModule) -> None:
    fake.providers = [ProviderUpkeep(key="corp", inactive_days=45)]
    elsewhere = await _linked(db, days_ago=100, provider="other")
    local = await _person(db)
    await upkeep.upkeep_once(db)
    assert await _active(db, elsewhere) and await _active(db, local)


# ── The directory ───────────────────────────────────────────────────────


async def test_the_disabled_and_the_missing_are_suspended(db: AsyncSession, fake: FakeModule) -> None:
    """CIS v8 6.2: leavers lose access without anyone signing in; ``unknown`` changes nothing."""
    fake.providers = [ProviderUpkeep(key="dir", directory=True)]
    stays = await _linked(db, days_ago=1, provider="dir", subject="s-stays")
    disabled = await _linked(db, days_ago=1, provider="dir", subject="s-disabled")
    gone = await _linked(db, days_ago=1, provider="dir", subject="s-gone")
    unknown = await _linked(db, days_ago=1, provider="dir", subject="s-unknown")
    fake.people = {"s-stays": "active", "s-disabled": "disabled", "s-gone": "missing", "s-unknown": "unknown"}
    report = await upkeep.upkeep_once(db)
    assert report is not None
    assert sorted(reason for _, reason in report.suspended) == ["directory_disabled", "directory_missing"]
    assert not await _active(db, disabled) and not await _active(db, gone)
    assert await _active(db, stays) and await _active(db, unknown)


async def test_the_suspension_is_in_the_audit_trail(db: AsyncSession, fake: FakeModule) -> None:
    from faden_backend.db.models.containers import AuditEvent

    fake.providers = [ProviderUpkeep(key="dir", directory=True)]
    gone = await _linked(db, days_ago=1, provider="dir", subject="s-gone")
    await upkeep.upkeep_once(db)
    event = await db.scalar(select(AuditEvent).where(
        AuditEvent.action == "user.suspend", AuditEvent.target_id == str(gone.id),
    ))
    assert event is not None and "directory_missing" in (event.metadata_json or "")


async def test_a_directory_that_cannot_be_asked_changes_nothing(db: AsyncSession, fake: FakeModule) -> None:
    fake.providers = [ProviderUpkeep(key="dir", directory=True)]
    user = await _linked(db, days_ago=1, provider="dir")
    fake.down = True
    report = await upkeep.upkeep_once(db)
    assert report is not None and report.errors and await _active(db, user)


async def test_a_superuser_the_directory_lost_is_left_to_an_administrator(
    db: AsyncSession, fake: FakeModule,
) -> None:
    fake.providers = [ProviderUpkeep(key="dir", directory=True)]
    admin = await _linked(db, days_ago=1, provider="dir", admin=True)
    await upkeep.upkeep_once(db)
    assert await _active(db, admin)


async def test_groups_follow_the_directory(db: AsyncSession, fake: FakeModule) -> None:
    """As at a sign-in: the provider's memberships follow it, an administrator's stay."""
    kept, dropped, by_hand = (Group(id=uuid.uuid4(), name=f"g-{uuid.uuid4().hex[:6]}") for _ in range(3))
    db.add_all([kept, dropped, by_hand])
    user = await _linked(db, days_ago=1, provider="dir", subject="s-1")
    db.add_all([
        UserGroup(user_id=user.id, group_id=dropped.id, source=accounts.membership_source("dir")),
        UserGroup(user_id=user.id, group_id=by_hand.id, source=None),
    ])
    await db.flush()
    fake.providers = [ProviderUpkeep(key="dir", directory=True)]
    fake.people, fake.manage_groups, fake.group_ids = {"s-1": "active"}, True, [kept.id]
    await upkeep.upkeep_once(db)
    groups = set(await db.scalars(select(UserGroup.group_id).where(UserGroup.user_id == user.id)))
    assert groups == {kept.id, by_hand.id}


async def test_without_manage_groups_the_directory_only_adds(db: AsyncSession, fake: FakeModule) -> None:
    """As at a sign-in: the rules' groups are added; taking away is manage_groups' alone."""
    given, earlier = (Group(id=uuid.uuid4(), name=f"g-{uuid.uuid4().hex[:6]}") for _ in range(2))
    db.add_all([given, earlier])
    user = await _linked(db, days_ago=1, provider="dir", subject="s-2")
    db.add(UserGroup(user_id=user.id, group_id=earlier.id, source=accounts.membership_source("dir")))
    await db.flush()
    fake.providers = [ProviderUpkeep(key="dir", directory=True)]
    fake.people, fake.manage_groups, fake.group_ids = {"s-2": "active"}, False, [given.id]
    await upkeep.upkeep_once(db)
    groups = set(await db.scalars(select(UserGroup.group_id).where(UserGroup.user_id == user.id)))
    assert groups == {given.id, earlier.id}


async def test_only_active_accounts_are_asked_about(db: AsyncSession, fake: FakeModule) -> None:
    """The upkeep reactivates nobody: suspended accounts are not even asked about."""
    fake.providers = [ProviderUpkeep(key="dir", directory=True)]
    suspended = await _linked(db, days_ago=1, provider="dir", subject="s-suspended")
    suspended.is_active = False
    await _linked(db, days_ago=1, provider="dir", subject="s-active")
    await db.flush()
    fake.people = {"s-suspended": "active", "s-active": "active"}
    await upkeep.upkeep_once(db)
    assert fake.asked == [["s-active"]] and not await _active(db, suspended)


async def test_without_a_module_nothing_happens(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert await upkeep.upkeep_once(db) is None
