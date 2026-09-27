"""Owners, sharing, immutable versions with pointers, the Agent Skills format.

- ``skills``: ``owner_id``, ``visibility`` and ``activation`` replace
  ``scope``; ``current_version`` becomes ``latest_version`` and
  ``published_version`` is what chats get. The tool lists, knowledge sources
  and triggers move into each version, so a rollback rolls them back.
  A slug is unique per owner instead of per tenant.
- ``skill_versions``: ``description``, ``frontmatter`` (SKILL.md's other
  fields), ``manifest`` (llm.port's), ``files`` and ``content_sha256``;
  ``frontmatter_yaml`` is taken apart into them.
- ``skill_grants``: sharing with users and groups.
- ``skill_usage_daily``: how often each version reached a chat.

Existing skills keep behaving as they did: all were made through the admin
pages, so the organization owns them and the whole tenant sees them; a
``global``/``tenant`` skill came into every chat, so it is ``always`` on,
and a ``workspace``/``assistant``/``user`` one only when it matched, so it is
``auto``.

Revision ID: 002_ownership_versions
Revises: 001_initial
Create Date: 2026-09-26 00:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "002_ownership_versions"
down_revision = "001_initial"
branch_labels = None
depends_on = None

_MANIFEST_KEYS = (
    "allowed_tools",
    "preferred_tools",
    "forbidden_tools",
    "knowledge_sources",
    "trigger_rules",
)
#: Frontmatter keys only the old llm.port documents had.
_LLMPORT_KEYS = {
    "name",
    "description",
    "scope",
    "status",
    "enabled",
    "priority",
    "tags",
    "title",
    "visibility",
    "activation",
    *_MANIFEST_KEYS,
}
_TRIGGER_KEYS = ("keywords", "file_types", "intents")


def upgrade() -> None:
    """Move skills to owners, grants, pointers and versioned manifests."""
    op.add_column("skills", sa.Column("owner_id", sa.String(128), nullable=True))
    op.add_column(
        "skills",
        sa.Column(
            "visibility",
            sa.String(16),
            nullable=False,
            server_default="private",
        ),
    )
    op.add_column(
        "skills",
        sa.Column("activation", sa.String(16), nullable=False, server_default="auto"),
    )
    op.add_column("skills", sa.Column("published_version", sa.Integer(), nullable=True))
    op.alter_column("skills", "current_version", new_column_name="latest_version")

    op.add_column(
        "skill_versions",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    for name in ("frontmatter", "manifest", "files"):
        op.add_column(
            "skill_versions",
            sa.Column(
                name,
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
    op.add_column(
        "skill_versions",
        sa.Column("content_sha256", sa.String(64), nullable=True),
    )

    _move_data()

    op.alter_column("skill_versions", "content_sha256", nullable=False)
    op.drop_column("skill_versions", "frontmatter_yaml")
    for name in ("scope", *_MANIFEST_KEYS):
        op.drop_column("skills", name)

    op.drop_constraint("uq_skill_tenant_slug", "skills", type_="unique")
    op.create_index(
        "uq_skill_owner_slug",
        "skills",
        ["tenant_id", sa.text("coalesce(owner_id, '')"), "slug"],
        unique=True,
    )
    op.create_index("ix_skills_tenant_owner", "skills", ["tenant_id", "owner_id"])

    op.create_table(
        "skill_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("principal_type", sa.String(16), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("access", sa.String(16), nullable=False, server_default="read"),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "skill_id",
            "principal_type",
            "principal_id",
            name="uq_skill_grant_principal",
        ),
    )
    op.create_index(
        "ix_skill_grants_principal",
        "skill_grants",
        ["principal_type", "principal_id"],
    )

    op.create_table(
        "skill_usage_daily",
        sa.Column(
            "skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Back to scopes and a mutable current version (grants and usage are lost)."""
    op.drop_table("skill_usage_daily")
    op.drop_index("ix_skill_grants_principal", table_name="skill_grants")
    op.drop_table("skill_grants")
    op.drop_index("ix_skills_tenant_owner", table_name="skills")
    op.drop_index("uq_skill_owner_slug", table_name="skills")

    op.add_column(
        "skills",
        sa.Column("scope", sa.String(32), nullable=False, server_default="tenant"),
    )
    for name in _MANIFEST_KEYS:
        op.add_column("skills", sa.Column(name, postgresql.JSONB(), nullable=True))
    op.add_column(
        "skill_versions",
        sa.Column("frontmatter_yaml", sa.Text(), nullable=True),
    )

    bind = op.get_bind()
    # The old constraint wants slugs unique per tenant; people's private
    # skills may share one now. The later ones get the id appended.
    bind.execute(
        sa.text(
            """
            UPDATE skills s SET slug = s.slug || '-' || left(s.id::text, 8)
            FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY tenant_id, slug ORDER BY created_at, id
                ) AS n
                FROM skills
            ) d
            WHERE d.id = s.id AND d.n > 1
            """,
        ),
    )
    bind.execute(
        sa.text(
            """
            UPDATE skills SET scope = CASE
                WHEN visibility = 'private' AND owner_id IS NOT NULL THEN 'user'
                WHEN activation = 'always' THEN 'tenant'
                ELSE 'workspace'
            END
            """,
        ),
    )
    rows = (
        bind.execute(
            sa.text(
                """
            SELECT s.id AS skill_id, v.id AS version_id, v.version,
                   s.latest_version, v.description, v.frontmatter, v.manifest
            FROM skills s JOIN skill_versions v ON v.skill_id = s.id
            """,
            ),
        )
        .mappings()
        .all()
    )
    for row in rows:
        manifest = row["manifest"] or {}
        document = {"description": row["description"], **(row["frontmatter"] or {})}
        document.update(manifest)
        bind.execute(
            sa.text("UPDATE skill_versions SET frontmatter_yaml = :y WHERE id = :id"),
            {
                "y": yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
                "id": row["version_id"],
            },
        )
        if row["version"] == row["latest_version"]:
            bind.execute(
                sa.text(
                    """
                    UPDATE skills SET
                        allowed_tools = CAST(:allowed AS jsonb),
                        preferred_tools = CAST(:preferred AS jsonb),
                        forbidden_tools = CAST(:forbidden AS jsonb),
                        knowledge_sources = CAST(:knowledge AS jsonb),
                        trigger_rules = CAST(:triggers AS jsonb)
                    WHERE id = :id
                    """,
                ),
                {
                    "allowed": _json_or_null(manifest.get("allowed_tools")),
                    "preferred": _json_or_null(manifest.get("preferred_tools")),
                    "forbidden": _json_or_null(manifest.get("forbidden_tools")),
                    "knowledge": _json_or_null(manifest.get("knowledge_sources")),
                    "triggers": _json_or_null(manifest.get("trigger_rules")),
                    "id": row["skill_id"],
                },
            )

    for name in ("description", "frontmatter", "manifest", "files", "content_sha256"):
        op.drop_column("skill_versions", name)
    op.alter_column("skills", "latest_version", new_column_name="current_version")
    for name in ("owner_id", "visibility", "activation", "published_version"):
        op.drop_column("skills", name)
    op.create_unique_constraint("uq_skill_tenant_slug", "skills", ["tenant_id", "slug"])


# ── Data ──────────────────────────────────────────────────────────────


def _move_data() -> None:
    bind = op.get_bind()
    skills = (
        bind.execute(
            sa.text(
                """
            SELECT id, slug, scope, status, latest_version, description,
                   allowed_tools, preferred_tools, forbidden_tools,
                   knowledge_sources, trigger_rules
            FROM skills
            """,
            ),
        )
        .mappings()
        .all()
    )
    for skill in skills:
        bind.execute(
            sa.text(
                """
                UPDATE skills
                SET visibility = 'tenant', activation = :activation,
                    published_version = :published
                WHERE id = :id
                """,
            ),
            {
                "activation": (
                    "always" if skill["scope"] in {"global", "tenant"} else "auto"
                ),
                "published": (
                    skill["latest_version"]
                    if skill["status"] in {"published", "archived"}
                    else None
                ),
                "id": skill["id"],
            },
        )
        row_manifest = _manifest({k: skill[k] for k in _MANIFEST_KEYS})
        versions = (
            bind.execute(
                sa.text(
                    """
                SELECT id, version, body_markdown, frontmatter_yaml
                FROM skill_versions WHERE skill_id = :id
                """,
                ),
                {"id": skill["id"]},
            )
            .mappings()
            .all()
        )
        for version in versions:
            fm = _load_frontmatter(version["frontmatter_yaml"])
            if version["version"] == skill["latest_version"]:
                # The skill's own fields were edited without a new version;
                # they are what the latest version was served with.
                manifest = row_manifest
                description = skill["description"] or ""
            else:
                manifest = _manifest({**row_manifest, **fm})
                description = str(fm.get("description") or skill["description"] or "")
            extra = _json_safe({k: v for k, v in fm.items() if k not in _LLMPORT_KEYS})
            body = version["body_markdown"] or ""
            digest = _content_sha256(
                name=skill["slug"],
                description=description,
                body=body,
                frontmatter=extra,
                manifest=manifest,
                files={},
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE skill_versions
                    SET description = :description,
                        frontmatter = CAST(:frontmatter AS jsonb),
                        manifest = CAST(:manifest AS jsonb),
                        content_sha256 = :digest
                    WHERE id = :id
                    """,
                ),
                {
                    "description": description,
                    "frontmatter": json.dumps(extra),
                    "manifest": json.dumps(manifest),
                    "digest": digest,
                    "id": version["id"],
                },
            )


def _load_frontmatter(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [part for part in value.replace(",", " ").split() if part]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _manifest(values: dict[str, Any]) -> dict[str, Any]:
    """The same shape ``bundle.normalize_manifest`` gives, forgiving bad types."""
    out: dict[str, Any] = {}
    for key in (
        "allowed_tools",
        "preferred_tools",
        "forbidden_tools",
        "knowledge_sources",
    ):
        items = _string_list(values.get(key))
        if items:
            out[key] = items
    rules = values.get("trigger_rules")
    if isinstance(rules, dict):
        clean = {k: _string_list(rules.get(k)) for k in _TRIGGER_KEYS}
        clean = {k: v for k, v in clean.items() if v}
        if clean:
            out["trigger_rules"] = clean
    return out


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _json_or_null(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _content_sha256(
    *,
    name: str,
    description: str,
    body: str,
    frontmatter: dict[str, Any],
    manifest: dict[str, Any],
    files: dict[str, Any],
) -> str:
    """``bundle.content_sha256``, copied so this migration never changes with it."""
    doc = {
        "name": name,
        "description": description,
        "body": body,
        "frontmatter": frontmatter,
        "manifest": manifest,
        "files": files,
    }
    canonical = json.dumps(
        doc,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
