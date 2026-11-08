"""Bypass grants: what the switch-on records, the device's report and the differences found (sdk/40 A3.1).

``approval_grant`` keeps the client's version and the person's optional reason (sdk/09 L-59);
``approval_grant_call`` which side ran the call and when the device reported it (L-61); ``approval_grant_flag``
a call the gateway counted that the device did not report, or the reverse. Bypass grants end with
``revoked_by = 'kill_switch'`` when the installation turns Bypass off, and are deleted with their calls after the
retention (L-65).

Revision ID: byp4ss
Revises: gr4ntx
Create Date: 2026-11-08 12:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "byp4ss"
down_revision = "gr4ntx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the columns and the table."""
    op.add_column("approval_grant", sa.Column("client_version", sa.String(64), nullable=True))
    op.add_column("approval_grant", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column("approval_grant_call", sa.Column("side", sa.String(8), nullable=True))
    op.add_column(
        "approval_grant_call", sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "approval_grant_call", sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "approval_grant_flag",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "grant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approval_grant.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("call_id", sa.String(128), nullable=False),
        sa.Column("tool_id", sa.String(256), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_approval_grant_flag_grant_id", "approval_grant_flag", ["grant_id"])
    op.create_index("ix_approval_grant_kind_ended", "approval_grant", ["kind", "expires_at"])


def downgrade() -> None:
    """Drop them again."""
    op.drop_index("ix_approval_grant_kind_ended", table_name="approval_grant")
    op.drop_index("ix_approval_grant_flag_grant_id", table_name="approval_grant_flag")
    op.drop_table("approval_grant_flag")
    op.drop_column("approval_grant_call", "checked_at")
    op.drop_column("approval_grant_call", "reported_at")
    op.drop_column("approval_grant_call", "side")
    op.drop_column("approval_grant", "reason")
    op.drop_column("approval_grant", "client_version")
