"""Data access for skills, with the rules on who may do what applied here.

Every method that reads or changes a skill takes the ``Principal`` it acts for
and checks it, so no route can forget: a skill the caller cannot see is "not
found", and one they can see but not change is "forbidden".
"""

from __future__ import annotations

import difflib
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from faden_skills.db.models.skill import (
    GrantAccess,
    PrincipalType,
    SkillActivation,
    SkillAssignmentModel,
    SkillGrantModel,
    SkillModel,
    SkillStatus,
    SkillUsageDailyModel,
    SkillVersionModel,
    SkillVisibility,
)
from faden_skills.services import bundle as fmt
from faden_skills.services.access import (
    CAP_CREATE,
    CAP_PUBLISH,
    CAP_SHARE,
    Access,
    Principal,
    access_to,
    is_org_visible,
    shared_with_clause,
    visible_clause,
)
from faden_skills.services.errors import (
    ConflictError,
    ForbiddenError,
    LimitError,
    NotFoundError,
)
from faden_skills.services.resolve import Candidate, Match, RequestContext, choose
from faden_skills.settings import settings

MAX_GRANTS_PER_SKILL = 200

#: ``list_skills`` views.
VIEW_ALL = "all"
VIEW_MINE = "mine"
VIEW_SHARED = "shared"
VIEW_ORG = "org"
VIEWS = (VIEW_ALL, VIEW_MINE, VIEW_SHARED, VIEW_ORG)


@dataclass
class VersionContent:
    """What a new version holds; ``None`` keeps the previous version's value."""

    description: str | None = None
    body_markdown: str | None = None
    frontmatter: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    files: dict[str, dict[str, str]] | None = None


@dataclass
class GrantSpec:
    """One grant, as a caller asks for it."""

    principal_type: str
    principal_id: str
    access: str = GrantAccess.READ.value


@dataclass
class Resolved:
    """A skill a chat request gets."""

    skill: SkillModel
    version: SkillVersionModel
    match: Match


def _require(access: Access, needed: Access, what: str) -> None:
    if access < needed:
        raise ForbiddenError(f"You may not {what} this skill.")


class SkillDao:
    """Encapsulates all DB operations for skills."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Reading ───────────────────────────────────────────────────────

    async def list_skills(
        self,
        principal: Principal,
        *,
        view: str = VIEW_ALL,
        search: str | None = None,
        status: str | None = None,
        visibility: str | None = None,
        usable: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[tuple[SkillModel, Access]], int]:
        """The skills *principal* can see, with what they may do with each.

        ``usable`` keeps the ones a chat can be given: published, enabled.
        """
        conditions = [visible_clause(principal)]
        if view == VIEW_MINE:
            conditions.append(
                and_(
                    SkillModel.tenant_id == principal.tenant_id,
                    SkillModel.owner_id == (principal.user_id or ""),
                ),
            )
        elif view == VIEW_SHARED:
            conditions.append(shared_with_clause(principal))
            conditions.append(
                or_(
                    SkillModel.owner_id.is_(None),
                    SkillModel.owner_id != (principal.user_id or ""),
                ),
            )
        elif view == VIEW_ORG:
            conditions.append(
                SkillModel.visibility.in_(
                    (SkillVisibility.TENANT.value, SkillVisibility.GLOBAL.value),
                ),
            )
        if status:
            conditions.append(SkillModel.status == status)
        if visibility:
            conditions.append(SkillModel.visibility == visibility)
        if usable:
            conditions += [
                SkillModel.status == SkillStatus.PUBLISHED.value,
                SkillModel.enabled.is_(True),
                SkillModel.published_version.is_not(None),
            ]
        if search:
            pattern = f"%{search}%"
            conditions.append(
                or_(
                    SkillModel.name.ilike(pattern),
                    SkillModel.slug.ilike(pattern),
                    SkillModel.description.ilike(pattern),
                ),
            )

        where = and_(*conditions)
        total = (
            await self._session.execute(
                select(func.count()).select_from(SkillModel).where(where),
            )
        ).scalar_one()
        rows = (
            (
                await self._session.execute(
                    select(SkillModel)
                    .where(where)
                    .order_by(SkillModel.updated_at.desc(), SkillModel.slug)
                    .offset(offset)
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )
        grants = await self.grants_for([s.id for s in rows])
        return [(s, access_to(principal, s, grants.get(s.id, []))) for s in rows], total

    async def get_skill(
        self,
        skill_id: uuid.UUID,
        principal: Principal,
        *,
        for_update: bool = False,
    ) -> tuple[SkillModel, Access]:
        """The skill and *principal*'s access, or ``NotFoundError``."""
        query = select(SkillModel).where(SkillModel.id == skill_id)
        if for_update:
            query = query.with_for_update()
        skill = (await self._session.execute(query)).scalar_one_or_none()
        if skill is None:
            raise NotFoundError("Skill not found.")
        grants = (await self.grants_for([skill.id])).get(skill.id, [])
        access = access_to(principal, skill, grants)
        if access == Access.NONE:
            raise NotFoundError("Skill not found.")
        return skill, access

    async def grants_for(
        self,
        skill_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[SkillGrantModel]]:
        """Each skill's grants."""
        if not skill_ids:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(SkillGrantModel)
                    .where(SkillGrantModel.skill_id.in_(skill_ids))
                    .order_by(SkillGrantModel.created_at),
                )
            )
            .scalars()
            .all()
        )
        out: dict[uuid.UUID, list[SkillGrantModel]] = {}
        for grant in rows:
            out.setdefault(grant.skill_id, []).append(grant)
        return out

    async def get_version(
        self,
        skill_id: uuid.UUID,
        version: int,
    ) -> SkillVersionModel | None:
        """One version of a skill."""
        return (
            await self._session.execute(
                select(SkillVersionModel).where(
                    SkillVersionModel.skill_id == skill_id,
                    SkillVersionModel.version == version,
                ),
            )
        ).scalar_one_or_none()

    async def require_version(
        self,
        skill_id: uuid.UUID,
        version: int,
    ) -> SkillVersionModel:
        """One version of a skill, or ``NotFoundError``."""
        found = await self.get_version(skill_id, version)
        if found is None:
            raise NotFoundError(f"Version {version} not found.")
        return found

    async def list_versions(self, skill_id: uuid.UUID) -> list[SkillVersionModel]:
        """Every version of a skill, newest first, without their content."""
        rows = await self._session.execute(
            select(SkillVersionModel)
            .options(
                load_only(
                    SkillVersionModel.id,
                    SkillVersionModel.skill_id,
                    SkillVersionModel.version,
                    SkillVersionModel.description,
                    SkillVersionModel.content_sha256,
                    SkillVersionModel.change_note,
                    SkillVersionModel.created_by,
                    SkillVersionModel.created_at,
                ),
            )
            .where(SkillVersionModel.skill_id == skill_id)
            .order_by(SkillVersionModel.version.desc()),
        )
        return list(rows.scalars().all())

    async def usage(
        self,
        skill_ids: list[uuid.UUID],
        *,
        days: int = 30,
    ) -> dict[uuid.UUID, int]:
        """How often each skill reached a chat in the last *days* days."""
        if not skill_ids:
            return {}
        since = datetime.now(UTC).date() - timedelta(days=days)
        rows = await self._session.execute(
            select(
                SkillUsageDailyModel.skill_id,
                func.sum(SkillUsageDailyModel.uses),
            )
            .where(
                SkillUsageDailyModel.skill_id.in_(skill_ids),
                SkillUsageDailyModel.day >= since,
            )
            .group_by(SkillUsageDailyModel.skill_id),
        )
        return {skill_id: int(total or 0) for skill_id, total in rows.all()}

    # ── Creating ──────────────────────────────────────────────────────

    async def create_skill(
        self,
        principal: Principal,
        *,
        title: str,
        slug: str | None,
        content: VersionContent,
        organization: bool = False,
        visibility: str = SkillVisibility.PRIVATE.value,
        activation: str = SkillActivation.AUTO.value,
        priority: int = 50,
        tags: list[str] | None = None,
        enabled: bool = True,
        publish: bool = False,
        change_note: str | None = None,
    ) -> SkillModel:
        """A new skill with its first version.

        ``organization`` makes the organization its owner (admins only);
        otherwise the caller owns it.
        """
        if organization:
            if not principal.is_admin:
                raise ForbiddenError(
                    "Only administrators create skills the organization owns.",
                )
            owner_id = None
        else:
            if not principal.user_id:
                raise ForbiddenError("A skill needs someone to own it.")
            if not principal.can(CAP_CREATE):
                raise ForbiddenError("You may not create skills.")
            owner_id = principal.user_id

        self._check_visibility_change(principal, visibility, activation)
        slug = fmt.validate_name(slug or fmt.slugify(title))
        title = (title or fmt.title_from_name(slug)).strip()[: fmt.TITLE_MAX]
        await self._check_slug_free(principal.tenant_id, owner_id, slug)
        if is_org_visible(visibility):
            await self._check_org_slug_free(principal.tenant_id, slug, None)
        if owner_id is not None:
            await self._check_user_limit(principal.tenant_id, owner_id)
        if publish and is_org_visible(visibility):
            await self._check_published_limit(principal.tenant_id, None)

        description = content.description or ""
        body = content.body_markdown or ""
        frontmatter = content.frontmatter or {}
        manifest = fmt.normalize_manifest(content.manifest)
        files = fmt.validate_files(content.files)
        fmt.check_content(description=description, body=body)

        skill = SkillModel(
            tenant_id=principal.tenant_id,
            name=title,
            slug=slug,
            description=description,
            owner_id=owner_id,
            visibility=visibility,
            activation=activation,
            status=(
                SkillStatus.PUBLISHED.value if publish else SkillStatus.DRAFT.value
            ),
            enabled=enabled,
            priority=priority,
            tags=tags or [],
            latest_version=1,
            published_version=1 if publish else None,
            created_by=principal.user_id or "service",
        )
        self._session.add(skill)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Two creates racing for one slug; the checks above passed both.
            raise ConflictError(f"A skill named '{slug}' already exists.") from exc

        self._session.add(
            SkillVersionModel(
                skill_id=skill.id,
                version=1,
                description=description,
                body_markdown=body,
                frontmatter=frontmatter,
                manifest=manifest,
                files=files,
                content_sha256=fmt.content_sha256(
                    name=slug,
                    description=description,
                    body=body,
                    frontmatter=frontmatter,
                    manifest=manifest,
                    files=files,
                ),
                change_note=change_note or "Initial version",
                created_by=principal.user_id or "service",
            ),
        )
        await self._session.flush()
        await self._session.refresh(skill)
        return skill

    # ── Changing ──────────────────────────────────────────────────────

    async def update_catalog(
        self,
        skill: SkillModel,
        access: Access,
        principal: Principal,
        **fields: Any,
    ) -> SkillModel:
        """Change the title, tags, activation, priority or enabled flag."""
        if not fields:
            return skill
        _require(access, Access.WRITE, "edit")
        owner_fields = {"activation", "priority", "enabled"} & fields.keys()
        if owner_fields:
            _require(access, Access.OWNER, f"change the {sorted(owner_fields)[0]} of")
        if "activation" in fields:
            self._check_visibility_change(
                principal,
                skill.visibility,
                fields["activation"],
            )
        if "name" in fields:
            title = (fields["name"] or "").strip()
            if not title:
                raise ConflictError("A skill needs a title.")
            skill.name = title[: fmt.TITLE_MAX]
        if "tags" in fields:
            skill.tags = list(fields["tags"] or [])
        for key in ("activation", "priority", "enabled"):
            if key in fields:
                setattr(skill, key, fields[key])
        await self._session.flush()
        # The flush expires ``updated_at``, which the database sets; read it
        # back now -- lazily, while the response is built, it would fail
        # with MissingGreenlet.
        await self._session.refresh(skill)
        return skill

    async def add_version(
        self,
        skill: SkillModel,
        access: Access,
        principal: Principal,
        content: VersionContent,
        *,
        base_version: int | None = None,
        publish: bool = False,
        change_note: str | None = None,
    ) -> tuple[SkillVersionModel, bool]:
        """Save *content* as the skill's next version; ``(version, created)``.

        Saving what the latest version already holds adds nothing (``created``
        is false). ``base_version`` is the version the editor started from:
        when someone saved in between, the save is refused rather than
        silently replacing their change.
        """
        _require(access, Access.WRITE, "edit")
        await self._session.refresh(skill, with_for_update=True)
        if base_version is not None and base_version != skill.latest_version:
            raise ConflictError(
                f"This skill changed since you opened it (it is now at "
                f"v{skill.latest_version}). Reload it to see the change.",
            )
        latest = await self.require_version(skill.id, skill.latest_version)

        description = (
            latest.description if content.description is None else content.description
        )
        body = (
            latest.body_markdown
            if content.body_markdown is None
            else content.body_markdown
        )
        frontmatter = (
            latest.frontmatter if content.frontmatter is None else content.frontmatter
        )
        manifest = (
            latest.manifest
            if content.manifest is None
            else fmt.normalize_manifest(content.manifest)
        )
        files = (
            latest.files if content.files is None else fmt.validate_files(content.files)
        )
        fmt.check_content(description=description, body=body)
        digest = fmt.content_sha256(
            name=skill.slug,
            description=description,
            body=body,
            frontmatter=frontmatter,
            manifest=manifest,
            files=files,
        )

        if digest == latest.content_sha256:
            version, created = latest, False
        else:
            version = SkillVersionModel(
                skill_id=skill.id,
                version=skill.latest_version + 1,
                description=description,
                body_markdown=body,
                frontmatter=frontmatter,
                manifest=manifest,
                files=files,
                content_sha256=digest,
                change_note=change_note,
                created_by=principal.user_id or "service",
            )
            self._session.add(version)
            skill.latest_version = version.version
            skill.description = description
            created = True
            await self._session.flush()

        if publish:
            await self._publish(skill, version.version)
        await self._session.flush()
        await self._session.refresh(skill)
        return version, created

    async def publish(
        self,
        skill: SkillModel,
        access: Access,
        principal: Principal,
        version: int | None = None,
    ) -> SkillModel:
        """Serve *version* (default: the latest) to chats -- or roll back to it."""
        _require(access, Access.WRITE, "publish")
        await self._publish(skill, version or skill.latest_version)
        await self._session.flush()
        await self._session.refresh(skill)
        return skill

    async def archive(
        self,
        skill: SkillModel,
        access: Access,
    ) -> SkillModel:
        """Stop serving the skill; its published version is kept for restoring."""
        _require(access, Access.OWNER, "archive")
        skill.status = SkillStatus.ARCHIVED.value
        await self._session.flush()
        await self._session.refresh(skill)
        return skill

    async def restore(
        self,
        skill: SkillModel,
        access: Access,
        principal: Principal,
    ) -> SkillModel:
        """Bring an archived skill back as it was."""
        _require(access, Access.OWNER, "restore")
        if skill.status != SkillStatus.ARCHIVED.value:
            return skill
        if skill.published_version is None:
            skill.status = SkillStatus.DRAFT.value
        else:
            if is_org_visible(skill.visibility):
                await self._check_published_limit(skill.tenant_id, skill.id)
            skill.status = SkillStatus.PUBLISHED.value
        await self._session.flush()
        await self._session.refresh(skill)
        return skill

    async def set_visibility(
        self,
        skill: SkillModel,
        access: Access,
        principal: Principal,
        visibility: str,
    ) -> SkillModel:
        """Change who sees the skill."""
        _require(access, Access.OWNER, "change who sees")
        if visibility == skill.visibility:
            return skill
        self._check_visibility_change(principal, visibility, skill.activation)
        if is_org_visible(visibility):
            await self._check_org_slug_free(skill.tenant_id, skill.slug, skill.id)
            if skill.status == SkillStatus.PUBLISHED.value and not is_org_visible(
                skill.visibility,
            ):
                await self._check_published_limit(skill.tenant_id, skill.id)
        skill.visibility = visibility
        await self._session.flush()
        await self._session.refresh(skill)
        return skill

    async def replace_grants(
        self,
        skill: SkillModel,
        access: Access,
        principal: Principal,
        grants: list[GrantSpec],
    ) -> list[SkillGrantModel]:
        """Share the skill with exactly *grants*."""
        _require(access, Access.OWNER, "share")
        if not principal.can(CAP_SHARE):
            raise ForbiddenError("You may not share skills.")
        if len(grants) > MAX_GRANTS_PER_SKILL:
            raise LimitError(
                f"A skill can be shared with at most {MAX_GRANTS_PER_SKILL} "
                "people and groups.",
            )
        wanted: dict[tuple[str, str], str] = {}
        for grant in grants:
            kind = grant.principal_type
            if kind not in {PrincipalType.USER.value, PrincipalType.GROUP.value}:
                raise ConflictError(f"Unknown principal type '{kind}'.")
            if grant.access not in {GrantAccess.READ.value, GrantAccess.WRITE.value}:
                raise ConflictError(f"Unknown access '{grant.access}'.")
            principal_id = grant.principal_id.strip()
            if not principal_id:
                raise ConflictError("A grant needs a principal id.")
            if kind == PrincipalType.USER.value and principal_id == skill.owner_id:
                continue  # the owner has all of it already
            key = (kind, principal_id)
            # The same principal twice: the stronger access wins.
            if wanted.get(key) != GrantAccess.WRITE.value:
                wanted[key] = grant.access

        await self._session.execute(
            delete(SkillGrantModel).where(SkillGrantModel.skill_id == skill.id),
        )
        for (kind, principal_id), level in wanted.items():
            self._session.add(
                SkillGrantModel(
                    skill_id=skill.id,
                    principal_type=kind,
                    principal_id=principal_id,
                    access=level,
                    created_by=principal.user_id or "service",
                ),
            )
        await self._session.flush()
        return (await self.grants_for([skill.id])).get(skill.id, [])

    async def delete_skill(self, skill: SkillModel, access: Access) -> None:
        """Delete the skill with all its versions, grants and assignments."""
        _require(access, Access.OWNER, "delete")
        await self._session.execute(delete(SkillModel).where(SkillModel.id == skill.id))
        await self._session.flush()

    async def remove_principal(self, principal_type: str, principal_id: str) -> int:
        """Drop every grant to a deleted user or group; how many there were."""
        result = await self._session.execute(
            delete(SkillGrantModel).where(
                SkillGrantModel.principal_type == principal_type,
                SkillGrantModel.principal_id == principal_id,
            ),
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    # ── Versions ──────────────────────────────────────────────────────

    def bundle_of(
        self,
        skill: SkillModel,
        version: SkillVersionModel,
    ) -> fmt.SkillBundle:
        """*version* of *skill* as the files an export holds."""
        catalog: dict[str, Any] = {"title": skill.name}
        if skill.tags:
            catalog["tags"] = list(skill.tags)
        catalog["priority"] = skill.priority
        catalog["activation"] = skill.activation
        return fmt.SkillBundle(
            name=skill.slug,
            description=version.description,
            body=version.body_markdown,
            frontmatter=dict(version.frontmatter or {}),
            manifest=dict(version.manifest or {}),
            catalog=catalog,
            files=dict(version.files or {}),
        )

    async def diff(
        self,
        skill: SkillModel,
        from_version: int,
        to_version: int,
    ) -> str:
        """A unified diff of two versions, file by file."""
        old = await self.require_version(skill.id, from_version)
        new = await self.require_version(skill.id, to_version)
        old_files = self._as_files(skill, old)
        new_files = self._as_files(skill, new)
        chunks: list[str] = []
        for path in sorted(old_files.keys() | new_files.keys()):
            before = old_files.get(path)
            after = new_files.get(path)
            if before == after:
                continue
            chunks.extend(
                difflib.unified_diff(
                    (before or "").splitlines(keepends=True),
                    (after or "").splitlines(keepends=True),
                    fromfile=(
                        f"v{from_version}/{path}" if before is not None else "/dev/null"
                    ),
                    tofile=(
                        f"v{to_version}/{path}" if after is not None else "/dev/null"
                    ),
                ),
            )
            if chunks and not chunks[-1].endswith("\n"):
                chunks.append("\n")
        return "".join(chunks)

    def _as_files(
        self,
        skill: SkillModel,
        version: SkillVersionModel,
    ) -> dict[str, str]:
        """A version as ``{path: text}``, binary files as a size line."""
        manifest_only = fmt.SkillBundle(
            name=skill.slug,
            description="",
            body="",
            manifest=dict(version.manifest or {}),
        )
        files: dict[str, str] = {
            fmt.SKILL_FILE: fmt.to_markdown(
                fmt.SkillBundle(
                    name=skill.slug,
                    description=version.description,
                    body=version.body_markdown,
                    frontmatter=dict(version.frontmatter or {}),
                ),
            ),
        }
        extension = fmt.extension_yaml(manifest_only)
        if extension is not None:
            files[fmt.EXTENSION_FILE] = extension
        for path, entry in (version.files or {}).items():
            if "text" in entry:
                files[path] = entry["text"]
            else:
                raw = fmt.file_bytes(entry)
                digest = hashlib.sha256(raw).hexdigest()[:12]
                files[path] = f"(binary, {len(raw)} bytes, sha256 {digest}…)\n"
        return files

    # ── Assignments (administrators) ──────────────────────────────────

    async def list_assignments(self, skill: SkillModel) -> list[SkillAssignmentModel]:
        """All assignments for a skill."""
        rows = await self._session.execute(
            select(SkillAssignmentModel)
            .where(SkillAssignmentModel.skill_id == skill.id)
            .order_by(SkillAssignmentModel.created_at),
        )
        return list(rows.scalars().all())

    async def create_assignment(
        self,
        skill: SkillModel,
        access: Access,
        *,
        target_type: str,
        target_id: str | None = None,
        enabled: bool = True,
        priority_override: int | None = None,
    ) -> SkillAssignmentModel:
        """Assign the skill to a target."""
        _require(access, Access.ADMIN, "assign")
        count = (
            await self._session.execute(
                select(func.count())
                .select_from(SkillAssignmentModel)
                .join(SkillModel, SkillAssignmentModel.skill_id == SkillModel.id)
                .where(SkillModel.tenant_id == skill.tenant_id),
            )
        ).scalar_one()
        if count >= settings.max_assignments_per_tenant:
            raise LimitError(
                f"Tenant has reached the maximum of "
                f"{settings.max_assignments_per_tenant} assignments.",
            )
        assignment = SkillAssignmentModel(
            skill_id=skill.id,
            target_type=target_type,
            target_id=target_id or None,
            enabled=enabled,
            priority_override=priority_override,
        )
        self._session.add(assignment)
        await self._session.flush()
        return assignment

    async def delete_assignment(
        self,
        skill: SkillModel,
        access: Access,
        assignment_id: uuid.UUID,
    ) -> None:
        """Remove one of *skill*'s assignments.

        Only one of this skill's: the id alone used to be enough, so any
        caller could delete any tenant's assignments.
        """
        _require(access, Access.ADMIN, "assign")
        result = await self._session.execute(
            delete(SkillAssignmentModel).where(
                SkillAssignmentModel.id == assignment_id,
                SkillAssignmentModel.skill_id == skill.id,
            ),
        )
        if not result.rowcount:  # type: ignore[attr-defined]
            raise NotFoundError("Assignment not found.")

    # ── Chats ─────────────────────────────────────────────────────────

    async def resolve(
        self,
        principal: Principal,
        ctx: RequestContext,
        *,
        max_results: int,
    ) -> list[Resolved]:
        """The skills a chat request from *principal* gets (see ``resolve``)."""
        # A chat is never resolved as an administrator: that would bring
        # other people's private skills into it.
        principal = Principal(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            group_ids=principal.group_ids,
        )
        rows = (
            await self._session.execute(
                select(SkillModel, SkillVersionModel.manifest)
                .join(
                    SkillVersionModel,
                    and_(
                        SkillVersionModel.skill_id == SkillModel.id,
                        SkillVersionModel.version == SkillModel.published_version,
                    ),
                )
                .where(
                    visible_clause(principal),
                    SkillModel.enabled.is_(True),
                    SkillModel.status == SkillStatus.PUBLISHED.value,
                ),
            )
        ).all()
        if not rows:
            return []
        skills = {skill.id: skill for skill, _ in rows}
        assignments: dict[uuid.UUID, list[SkillAssignmentModel]] = {}
        for assignment in (
            await self._session.execute(
                select(SkillAssignmentModel).where(
                    SkillAssignmentModel.skill_id.in_(list(skills)),
                ),
            )
        ).scalars():
            assignments.setdefault(assignment.skill_id, []).append(assignment)

        candidates = [
            Candidate(
                skill_id=skill.id,
                slug=skill.slug,
                owner_id=skill.owner_id,
                visibility=skill.visibility,
                activation=skill.activation,
                priority=skill.priority,
                tags=list(skill.tags or []),
                version=skill.published_version or 0,
                manifest=manifest or {},
                assignments=assignments.get(skill.id, []),
            )
            for skill, manifest in rows
        ]
        matches = choose(
            candidates,
            ctx,
            max_results=max_results,
            max_explicit=settings.max_explicit_skills,
        )
        if not matches:
            return []
        keys = [(m.candidate.skill_id, m.candidate.version) for m in matches]
        versions = {
            (v.skill_id, v.version): v
            for v in (
                await self._session.execute(
                    select(SkillVersionModel).where(
                        tuple_(
                            SkillVersionModel.skill_id,
                            SkillVersionModel.version,
                        ).in_(
                            keys,
                        ),
                    ),
                )
            ).scalars()
        }
        return [
            Resolved(skills[m.candidate.skill_id], versions[key], m)
            for m, key in zip(matches, keys, strict=True)
            if key in versions
        ]

    async def record_usage(
        self,
        skill_id: uuid.UUID,
        version: int,
        *,
        day: date | None = None,
    ) -> None:
        """Count one use of *version* today."""
        stmt = pg_insert(SkillUsageDailyModel).values(
            skill_id=skill_id,
            version=version,
            day=day or datetime.now(UTC).date(),
            uses=1,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    SkillUsageDailyModel.skill_id,
                    SkillUsageDailyModel.version,
                    SkillUsageDailyModel.day,
                ],
                set_={"uses": SkillUsageDailyModel.uses + 1},
            ),
        )

    # ── Checks ────────────────────────────────────────────────────────

    async def _publish(self, skill: SkillModel, version: int) -> None:
        await self.require_version(skill.id, version)
        if (
            is_org_visible(skill.visibility)
            and skill.status != SkillStatus.PUBLISHED.value
        ):
            await self._check_published_limit(skill.tenant_id, skill.id)
        skill.published_version = version
        skill.status = SkillStatus.PUBLISHED.value

    @staticmethod
    def _check_visibility_change(
        principal: Principal,
        visibility: str,
        activation: str,
    ) -> None:
        """Refuse what *principal* may not set: *visibility* with *activation*."""
        if visibility not in {v.value for v in SkillVisibility}:
            raise ConflictError(f"Unknown visibility '{visibility}'.")
        if activation not in {a.value for a in SkillActivation}:
            raise ConflictError(f"Unknown activation '{activation}'.")
        if visibility == SkillVisibility.GLOBAL.value and not principal.is_admin:
            raise ForbiddenError(
                "Only administrators make skills visible to every tenant.",
            )
        if visibility == SkillVisibility.TENANT.value and not principal.can(
            CAP_PUBLISH,
        ):
            raise ForbiddenError(
                "You may not publish skills to the whole organization.",
            )
        if (
            is_org_visible(visibility)
            and activation == SkillActivation.ALWAYS.value
            and not principal.is_admin
        ):
            raise ForbiddenError(
                "An always-on skill the whole organization sees goes into "
                "everyone's chats; only administrators can set one up. Use "
                "'auto' or 'manual' activation.",
            )

    async def _check_slug_free(
        self,
        tenant_id: str,
        owner_id: str | None,
        slug: str,
    ) -> None:
        owner_match = (
            SkillModel.owner_id.is_(None)
            if owner_id is None
            else SkillModel.owner_id == owner_id
        )
        taken = (
            await self._session.execute(
                select(SkillModel.id).where(
                    SkillModel.tenant_id == tenant_id,
                    owner_match,
                    SkillModel.slug == slug,
                ),
            )
        ).first()
        if taken is not None:
            raise ConflictError(f"A skill named '{slug}' already exists.")

    async def _check_org_slug_free(
        self,
        tenant_id: str,
        slug: str,
        skill_id: uuid.UUID | None,
    ) -> None:
        """Two skills the whole organization sees cannot share a name."""
        query = select(SkillModel.id).where(
            SkillModel.slug == slug,
            SkillModel.visibility.in_(
                (SkillVisibility.TENANT.value, SkillVisibility.GLOBAL.value),
            ),
            or_(
                SkillModel.tenant_id == tenant_id,
                SkillModel.visibility == SkillVisibility.GLOBAL.value,
            ),
        )
        if skill_id is not None:
            query = query.where(SkillModel.id != skill_id)
        if (await self._session.execute(query)).first() is not None:
            raise ConflictError(
                f"The organization already has a skill named '{slug}'. "
                "Rename this one before sharing it with everyone.",
            )

    async def _check_user_limit(self, tenant_id: str, owner_id: str) -> None:
        count = (
            await self._session.execute(
                select(func.count())
                .select_from(SkillModel)
                .where(
                    SkillModel.tenant_id == tenant_id,
                    SkillModel.owner_id == owner_id,
                ),
            )
        ).scalar_one()
        if count >= settings.max_skills_per_user:
            raise LimitError(
                "You have reached the maximum of "
                f"{settings.max_skills_per_user} skills.",
            )

    async def _check_published_limit(
        self,
        tenant_id: str,
        skill_id: uuid.UUID | None,
    ) -> None:
        query = (
            select(func.count())
            .select_from(SkillModel)
            .where(
                SkillModel.tenant_id == tenant_id,
                SkillModel.status == SkillStatus.PUBLISHED.value,
                SkillModel.visibility.in_(
                    (SkillVisibility.TENANT.value, SkillVisibility.GLOBAL.value),
                ),
            )
        )
        if skill_id is not None:
            query = query.where(SkillModel.id != skill_id)
        count = (await self._session.execute(query)).scalar_one()
        if count >= settings.max_published_skills_per_tenant:
            raise LimitError(
                f"The organization has reached the maximum of "
                f"{settings.max_published_skills_per_tenant} published skills.",
            )
