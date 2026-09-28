"""Who may use an agent, and why.

Default deny: an agent is usable through a grant to the user, or to a group
the user is in. Maintainers (and administrators) may use it whatever its
grants, drafts included. Enterprise access rules (``services.extensions``) can
only take access away.

An agent whose published version carries settings of an extension that is
not installed -- Enterprise gone, a licence expired -- is usable by its
maintainers only: those settings restrict access or data, and nothing would
enforce them.
"""

from __future__ import annotations

from dataclasses import dataclass

from faden_backend.db.dao.agents_dao import AgentsDAO
from faden_backend.db.models.agents import Agent, AgentMemberRole, AgentStatus, PrincipalType
from faden_backend.db.models.users import User
from faden_backend.services.extensions import ExtensionRegistry, registry


@dataclass(frozen=True)
class AccessDecision:
    """The outcome, a stable code, and a sentence for people."""

    allowed: bool
    code: str
    detail: str


def unenforced_extensions(extensions: dict[str, object], reg: ExtensionRegistry = registry) -> list[str]:
    """Keys of *extensions* no installed extension enforces."""
    return sorted(key for key in extensions if key not in reg.version_extensions)


async def is_maintainer(dao: AgentsDAO, agent: Agent, user: User) -> bool:
    """Whether *user* looks after *agent* (administrators look after every agent)."""
    if user.is_superuser:
        return True
    return await dao.member_role(agent.id, user.id) == AgentMemberRole.MAINTAINER.value


async def _grant_path(dao: AgentsDAO, agent: Agent, user: User) -> str | None:
    """How *user* is granted *agent*, as a sentence; ``None`` when not at all."""
    groups = await dao.user_group_ids(user.id)
    via: str | None = None
    for grant in await dao.grants(agent.id):
        if grant.principal_type == PrincipalType.USER and grant.principal_id == user.id:
            return "Allowed through a grant to the user."
        if via is None and grant.principal_type == PrincipalType.GROUP and grant.principal_id in groups:
            names = await dao.existing_groups({grant.principal_id})
            via = f"Allowed through team {names.get(grant.principal_id, grant.principal_id)}."
    return via


async def _closed(dao: AgentsDAO, agent: Agent, reg: ExtensionRegistry) -> AccessDecision | None:
    """Why *agent* is closed to everyone but its maintainers, if it is."""
    if agent.status != AgentStatus.PUBLISHED or agent.published_version_id is None:
        return AccessDecision(False, "not_published", "The agent has no published version yet.")
    version = await dao.version_by_id(agent.published_version_id)
    missing = unenforced_extensions(version.extensions if version else {}, reg)
    if missing:
        return AccessDecision(
            False,
            "needs_enterprise",
            "The agent uses settings that need Fadenstack Enterprise ("
            + ", ".join(missing)
            + "); until then only its maintainers can use it.",
        )
    return None


async def decide(
    dao: AgentsDAO,
    agent: Agent,
    user: User,
    reg: ExtensionRegistry = registry,
) -> AccessDecision:
    """Whether *user* may use *agent*, and through what."""
    if not user.is_active:
        return AccessDecision(False, "user_inactive", "The user is deactivated.")
    if agent.status == AgentStatus.RETIRED:
        return AccessDecision(False, "agent_retired", "The agent is retired.")
    if await is_maintainer(dao, agent, user):
        who = "an administrator" if user.is_superuser else "a maintainer"
        return AccessDecision(True, "maintainer", f"Allowed as {who} of the agent.")
    closed = await _closed(dao, agent, reg)
    if closed is not None:
        return closed
    return await _granted(dao, agent, user, reg)


async def _granted(dao: AgentsDAO, agent: Agent, user: User, reg: ExtensionRegistry) -> AccessDecision:
    """The grants, then the access rules: every rule can still deny."""
    via = await _grant_path(dao, agent, user)
    if via is None:
        return AccessDecision(
            False, "no_grant", "Neither the user nor a team of theirs is granted the agent.",
        )
    for rule in reg.access_rules:
        reason = await rule(user.id, agent.id)
        if reason:
            return AccessDecision(False, "denied_by_rule", reason)
    return AccessDecision(True, "granted", via)
