"""Grants (``approval_grant``) and the calls they covered (``approval_grant_call``).

A person lets one agent on one device run, for a while, what would ask: one tool (``time``) or reads and writes
in named places (``work``). The gateway counts each covered call against the grant's budget in one conditional
update, and keeps the call as evidence (sdk/38 §6).

Revision ID: gr4nts
Revises: 4nsw3rsc
Create Date: 2026-11-07 16:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "gr4nts"
down_revision = "4nsw3rsc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create both tables."""
    op.create_table(
        "approval_grant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("conversation_id", sa.String(128), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=True),
        sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(16), nullable=True),
        sa.Column("policy_version", sa.String(64), nullable=True),
        sa.Column("evidence_level", sa.String(16), nullable=False),
        sa.Column("shown_text_hash", sa.String(64), nullable=True),
    )
    op.create_index("ix_approval_grant_owner", "approval_grant", ["tenant_id", "user_id", "agent_id", "device_id"])
    op.create_table(
        "approval_grant_call",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "grant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approval_grant.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("call_id", sa.String(128), nullable=False),
        sa.Column("tool_id", sa.String(256), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("arguments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_approval_grant_call_grant_id", "approval_grant_call", ["grant_id"])


def downgrade() -> None:
    """Drop them again."""
    op.drop_index("ix_approval_grant_call_grant_id", table_name="approval_grant_call")
    op.drop_table("approval_grant_call")
    op.drop_index("ix_approval_grant_owner", table_name="approval_grant")
    op.drop_table("approval_grant")
