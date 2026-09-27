"""Who is asking, and what they may do with a skill.

The backend decides what a user may do in general (RBAC: may they create
skills, share them, publish them to the whole tenant, administer them all)
and sends that with each request as capabilities. This module decides what
they may do with one skill: that follows from who owns it, who it is shared
with, and who can see it.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, and_, exists, false, or_, tuple_

from faden_skills.db.models.skill import (
    GrantAccess,
    PrincipalType,
    SkillGrantModel,
    SkillModel,
    SkillVisibility,
)

#: What the backend may say a user can do.
CAP_ADMIN = "admin"
CAP_CREATE = "create"
CAP_SHARE = "share"
CAP_PUBLISH = "publish"
KNOWN_CAPS = frozenset({CAP_ADMIN, CAP_CREATE, CAP_SHARE, CAP_PUBLISH})

_ORG_VISIBLE = (SkillVisibility.TENANT.value, SkillVisibility.GLOBAL.value)


class Access(enum.IntEnum):
    """What someone may do with one skill; each level includes the ones below."""

    NONE = 0
    #: See it and use it in chats.
    READ = 1
    #: Edit it: new versions, publishing a version, title and tags.
    WRITE = 2
    #: Also share it, change who sees it, archive and delete it.
    OWNER = 3
    #: Everything, on every skill in the tenant.
    ADMIN = 4

    @property
    def label(self) -> str:
        """The level's name for API responses."""
        return self.name.lower()


@dataclass(frozen=True)
class Principal:
    """The person a request acts for."""

    tenant_id: str
    user_id: str | None
    group_ids: frozenset[str] = field(default_factory=frozenset)
    caps: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_admin(self) -> bool:
        """Whether they administer every skill in the tenant."""
        return CAP_ADMIN in self.caps

    def can(self, cap: str) -> bool:
        """Whether RBAC lets them do *cap* at all (admins can do everything)."""
        return self.is_admin or cap in self.caps

    def principal_keys(self) -> list[tuple[str, str]]:
        """The ``(type, id)`` pairs a grant to them could name."""
        keys: list[tuple[str, str]] = []
        if self.user_id:
            keys.append((PrincipalType.USER.value, self.user_id))
        keys.extend((PrincipalType.GROUP.value, g) for g in sorted(self.group_ids))
        return keys


def access_to(
    principal: Principal,
    skill: SkillModel,
    grants: Iterable[SkillGrantModel] = (),
) -> Access:
    """What *principal* may do with *skill*, given the skill's grants."""
    same_tenant = skill.tenant_id == principal.tenant_id
    if principal.is_admin and same_tenant:
        return Access.ADMIN
    if same_tenant and skill.owner_id and skill.owner_id == principal.user_id:
        return Access.OWNER

    level = Access.NONE
    if same_tenant:
        keys = set(principal.principal_keys())
        for grant in grants:
            if (grant.principal_type, grant.principal_id) not in keys:
                continue
            granted = (
                Access.WRITE if grant.access == GrantAccess.WRITE.value else Access.READ
            )
            level = max(level, granted)
    if skill.visibility == SkillVisibility.GLOBAL.value or (
        same_tenant and skill.visibility == SkillVisibility.TENANT.value
    ):
        level = max(level, Access.READ)
    return level


def visible_clause(principal: Principal) -> ColumnElement[bool]:
    """SQL for "*principal* can see this skill" -- the same rule as ``access_to``."""
    in_tenant = SkillModel.tenant_id == principal.tenant_id
    is_global = SkillModel.visibility == SkillVisibility.GLOBAL.value
    if principal.is_admin:
        return or_(in_tenant, is_global)

    conditions: list[ColumnElement[bool]] = [
        is_global,
        and_(in_tenant, SkillModel.visibility == SkillVisibility.TENANT.value),
    ]
    if principal.user_id:
        conditions.append(and_(in_tenant, SkillModel.owner_id == principal.user_id))
    keys = principal.principal_keys()
    if keys:
        conditions.append(
            and_(
                in_tenant,
                exists().where(
                    SkillGrantModel.skill_id == SkillModel.id,
                    tuple_(
                        SkillGrantModel.principal_type,
                        SkillGrantModel.principal_id,
                    ).in_(keys),
                ),
            ),
        )
    return or_(*conditions)


def shared_with_clause(principal: Principal) -> ColumnElement[bool]:
    """SQL for "this skill is shared with *principal* by grant"."""
    keys = principal.principal_keys()
    if not keys:
        return false()
    return exists().where(
        SkillGrantModel.skill_id == SkillModel.id,
        tuple_(SkillGrantModel.principal_type, SkillGrantModel.principal_id).in_(
            keys,
        ),
    )


def is_org_visible(visibility: str) -> bool:
    """Whether a skill with *visibility* is seen by the whole tenant or more."""
    return visibility in _ORG_VISIBLE
