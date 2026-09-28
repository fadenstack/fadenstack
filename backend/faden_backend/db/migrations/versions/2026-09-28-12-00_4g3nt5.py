"""Agents: versions, grants, maintainers, refresh tokens; developer mode on users.

An agent is what an SDK integration (a TIA Portal or EPLAN add-in) runs
under: its instructions, model and tool contract are versioned here, and who
may use it is granted to users and groups -- default deny. Agent sign-ins
keep one refresh token per device, stored as a SHA-256 hash.

Revision ID: 4g3nt5
Revises: r4yt1sca
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "4g3nt5"
down_revision = "r4yt1sca"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_JSONB = postgresql.JSONB()


def _now() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    agent_status = postgresql.ENUM("draft", "published", "retired", name="agent_status", create_type=False)
    local_mcp = postgresql.ENUM("disable", "allow", name="agent_local_mcp_mode", create_type=False)
    principal = postgresql.ENUM("user", "group", name="agent_principal_type", create_type=False)
    for enum in (agent_status, local_mcp, principal):
        enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "agents",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default="default"),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", agent_status, nullable=False, server_default="draft"),
        sa.Column("published_version_id", _UUID, nullable=True),
        sa.Column("created_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        _now(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_agents_tenant_slug"),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])

    op.create_table(
        "agent_versions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("model_alias", sa.String(256), nullable=True),
        sa.Column("knowledge", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("server_tools", _JSONB, nullable=True),
        sa.Column("host_tool_contract", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("local_mcp_mode", local_mcp, nullable=False, server_default="disable"),
        sa.Column("ui", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("extensions", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        _now(),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_foreign_key(
        "fk_agents_published_version",
        "agents",
        "agent_versions",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "agent_grants",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_type", principal, nullable=False),
        sa.Column("principal_id", _UUID, nullable=False),
        sa.Column("created_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        _now(),
        sa.UniqueConstraint("agent_id", "principal_type", "principal_id", name="uq_agent_grants_principal"),
    )
    op.create_index("ix_agent_grants_agent_id", "agent_grants", ["agent_id"])
    op.create_index("ix_agent_grants_principal", "agent_grants", ["principal_type", "principal_id"])

    op.create_table(
        "agent_members",
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="maintainer"),
        _now(),
    )
    op.create_index("ix_agent_members_user_id", "agent_members", ["user_id"])

    op.create_table(
        "agent_refresh_tokens",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("family_id", _UUID, nullable=False),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default="default"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("device_label", sa.String(200), nullable=True),
        _now(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", _UUID, nullable=True),
    )
    op.create_index("ix_agent_refresh_tokens_family_id", "agent_refresh_tokens", ["family_id"])
    op.create_index("ix_agent_refresh_tokens_agent_id", "agent_refresh_tokens", ["agent_id"])
    op.create_index("ix_agent_refresh_tokens_user_id", "agent_refresh_tokens", ["user_id"])

    op.add_column(
        "user",
        sa.Column("developer_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("user", sa.Column("developer_mode_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "developer_mode_until")
    op.drop_column("user", "developer_mode")
    op.drop_table("agent_refresh_tokens")
    op.drop_table("agent_members")
    op.drop_table("agent_grants")
    op.drop_constraint("fk_agents_published_version", "agents", type_="foreignkey")
    op.drop_table("agent_versions")
    op.drop_table("agents")
    for name in ("agent_principal_type", "agent_local_mcp_mode", "agent_status"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
