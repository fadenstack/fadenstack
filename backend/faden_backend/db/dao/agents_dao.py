"""DAO for agents, their versions, grants, members and refresh tokens."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.agents import (
    Agent,
    AgentGrant,
    AgentMember,
    AgentMemberRole,
    AgentRefreshToken,
    AgentStatus,
    AgentVersion,
    PrincipalType,
)
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.users import User


class AgentsDAO:
    """Reads and writes the agent tables."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    # -- agents ---------------------------------------------------------------

    async def get(self, tenant_id: str, ref: str) -> Agent | None:
        """The agent by id or by slug, within the tenant."""
        query = select(Agent).where(Agent.tenant_id == tenant_id)
        try:
            agent_id = uuid.UUID(ref)
        except ValueError:
            query = query.where(Agent.slug == ref)
        else:
            query = query.where(or_(Agent.id == agent_id, Agent.slug == ref))
        return (await self.session.execute(query)).scalar_one_or_none()

    async def slug_taken(self, tenant_id: str, slug: str) -> bool:
        """Whether the tenant has an agent with *slug*."""
        found = await self.session.execute(
            select(Agent.id).where(Agent.tenant_id == tenant_id, Agent.slug == slug),
        )
        return found.first() is not None

    async def list_all(self, tenant_id: str) -> list[Agent]:
        """Every agent of the tenant, by name."""
        rows = await self.session.execute(
            select(Agent).where(Agent.tenant_id == tenant_id).order_by(Agent.name),
        )
        return list(rows.scalars())

    async def list_for_user(self, tenant_id: str, user_id: uuid.UUID) -> list[Agent]:
        """Agents the user maintains, or is granted directly or through a group."""
        groups = select(UserGroup.group_id).where(UserGroup.user_id == user_id)
        granted = select(AgentGrant.agent_id).where(
            or_(
                (AgentGrant.principal_type == PrincipalType.USER) & (AgentGrant.principal_id == user_id),
                (AgentGrant.principal_type == PrincipalType.GROUP) & AgentGrant.principal_id.in_(groups),
            ),
        )
        maintained = select(AgentMember.agent_id).where(AgentMember.user_id == user_id)
        rows = await self.session.execute(
            select(Agent)
            .where(
                Agent.tenant_id == tenant_id,
                or_(
                    Agent.id.in_(maintained),
                    (Agent.status == AgentStatus.PUBLISHED) & Agent.id.in_(granted),
                ),
            )
            .order_by(Agent.name),
        )
        return list(rows.scalars())

    async def add(self, agent: Agent) -> Agent:
        """Store a new agent."""
        self.session.add(agent)
        await self.session.flush()
        return agent

    async def remove(self, agent: Agent) -> None:
        """Delete the agent; its versions, grants, members and sign-ins go with it (``ON DELETE CASCADE``)."""
        agent_id = agent.id
        # The agent points at a version and its versions at it: let go of the version first.
        agent.published_version_id = None
        await self.session.flush()
        self.session.expunge(agent)
        await self.session.execute(delete(Agent).where(Agent.id == agent_id))

    # -- versions -------------------------------------------------------------

    async def versions(self, agent_id: uuid.UUID) -> list[AgentVersion]:
        """The agent's versions, newest first."""
        rows = await self.session.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.version.desc()),
        )
        return list(rows.scalars())

    async def version(self, agent_id: uuid.UUID, number: int) -> AgentVersion | None:
        """One version of the agent, by number."""
        found = await self.session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent_id, AgentVersion.version == number),
        )
        return found.scalar_one_or_none()

    async def version_by_id(self, version_id: uuid.UUID | None) -> AgentVersion | None:
        """A version by id; ``None`` for no id."""
        if version_id is None:
            return None
        return await self.session.get(AgentVersion, version_id)

    async def latest_version(self, agent_id: uuid.UUID) -> AgentVersion | None:
        """The agent's newest version."""
        found = await self.session.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.version.desc())
            .limit(1),
        )
        return found.scalar_one_or_none()

    async def add_version(self, agent_id: uuid.UUID, fields: dict[str, Any]) -> AgentVersion:
        """The next version of the agent, numbered after the last one."""
        # One row lock on the agent orders concurrent saves: no two get the
        # same number.
        await self.session.execute(select(Agent.id).where(Agent.id == agent_id).with_for_update())
        last = await self.session.scalar(
            select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent_id),
        )
        version = AgentVersion(agent_id=agent_id, version=(last or 0) + 1, **fields)
        self.session.add(version)
        await self.session.flush()
        return version

    # -- grants and members ---------------------------------------------------

    async def grants(self, agent_id: uuid.UUID) -> list[AgentGrant]:
        """The agent's grants, oldest first."""
        rows = await self.session.execute(
            select(AgentGrant).where(AgentGrant.agent_id == agent_id).order_by(AgentGrant.created_at),
        )
        return list(rows.scalars())

    async def replace_grants(
        self,
        agent_id: uuid.UUID,
        principals: Sequence[tuple[PrincipalType, uuid.UUID]],
        created_by: uuid.UUID,
    ) -> list[AgentGrant]:
        """Replace the agent's grants; duplicates are dropped."""
        await self.session.execute(delete(AgentGrant).where(AgentGrant.agent_id == agent_id))
        grants = [
            AgentGrant(agent_id=agent_id, principal_type=kind, principal_id=pid, created_by=created_by)
            for kind, pid in dict.fromkeys(principals)
        ]
        self.session.add_all(grants)
        await self.session.flush()
        return grants

    async def members(self, agent_id: uuid.UUID) -> list[AgentMember]:
        """The agent's members, oldest first."""
        rows = await self.session.execute(
            select(AgentMember).where(AgentMember.agent_id == agent_id).order_by(AgentMember.created_at),
        )
        return list(rows.scalars())

    async def maintains_any(self, user_id: uuid.UUID) -> bool:
        """Whether *user_id* maintains at least one agent."""
        return (
            await self.session.scalar(
                select(AgentMember.agent_id)
                .where(AgentMember.user_id == user_id, AgentMember.role == AgentMemberRole.MAINTAINER.value)
                .limit(1),
            )
        ) is not None

    async def member_role(self, agent_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
        """The role of *user_id* on the agent, if any."""
        return await self.session.scalar(
            select(AgentMember.role).where(AgentMember.agent_id == agent_id, AgentMember.user_id == user_id),
        )

    async def replace_members(self, agent_id: uuid.UUID, members: dict[uuid.UUID, str]) -> list[AgentMember]:
        """Replace the agent's members."""
        await self.session.execute(delete(AgentMember).where(AgentMember.agent_id == agent_id))
        rows = [AgentMember(agent_id=agent_id, user_id=uid, role=role) for uid, role in members.items()]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def add_member(self, agent_id: uuid.UUID, user_id: uuid.UUID, role: str) -> None:
        """Make *user_id* a member of the agent."""
        self.session.add(AgentMember(agent_id=agent_id, user_id=user_id, role=role))
        await self.session.flush()

    async def user_group_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """The groups *user_id* is in."""
        rows = await self.session.execute(select(UserGroup.group_id).where(UserGroup.user_id == user_id))
        return set(rows.scalars())

    async def existing_users(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Those of *ids* that are users, with their e-mail addresses."""
        if not ids:
            return {}
        rows = await self.session.execute(select(User.id, User.email).where(User.id.in_(ids)))
        return dict(rows.tuples().all())

    async def existing_groups(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Those of *ids* that are groups, with their names."""
        if not ids:
            return {}
        rows = await self.session.execute(select(Group.id, Group.name).where(Group.id.in_(ids)))
        return dict(rows.tuples().all())

    # -- refresh tokens -------------------------------------------------------

    async def add_refresh_token(self, token: AgentRefreshToken) -> AgentRefreshToken:
        """Store a refresh token row."""
        self.session.add(token)
        await self.session.flush()
        return token

    async def refresh_token_by_hash(self, token_hash: str) -> AgentRefreshToken | None:
        """The refresh token row with *token_hash*, locked for the refresh."""
        found = await self.session.execute(
            select(AgentRefreshToken).where(AgentRefreshToken.token_hash == token_hash).with_for_update(),
        )
        return found.scalar_one_or_none()

    async def revoke_family(self, family_id: uuid.UUID, when: datetime) -> int:
        """End every token of a sign-in; returns how many were still live."""
        result = await self.session.execute(
            update(AgentRefreshToken)
            .where(AgentRefreshToken.family_id == family_id, AgentRefreshToken.revoked_at.is_(None))
            .values(revoked_at=when),
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def active_families(
        self,
        *,
        now: datetime,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
    ) -> list[AgentRefreshToken]:
        """The current token of every sign-in that has not ended."""
        query = select(AgentRefreshToken).where(
            AgentRefreshToken.revoked_at.is_(None),
            AgentRefreshToken.replaced_by.is_(None),
            AgentRefreshToken.expires_at > now,
            AgentRefreshToken.idle_expires_at > now,
        )
        if user_id is not None:
            query = query.where(AgentRefreshToken.user_id == user_id)
        if agent_id is not None:
            query = query.where(AgentRefreshToken.agent_id == agent_id)
        rows = await self.session.execute(query.order_by(AgentRefreshToken.created_at.desc()))
        return list(rows.scalars())

    async def family(self, family_id: uuid.UUID) -> AgentRefreshToken | None:
        """Any token of the family, to tell whose it is."""
        found = await self.session.execute(
            select(AgentRefreshToken).where(AgentRefreshToken.family_id == family_id).limit(1),
        )
        return found.scalar_one_or_none()
