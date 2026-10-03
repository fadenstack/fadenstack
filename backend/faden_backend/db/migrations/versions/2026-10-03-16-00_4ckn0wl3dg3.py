"""Needs attention items an administrator has muted.

Revision ID: 4ckn0wl3dg3
Revises: st0pp1ng
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "4ckn0wl3dg3"
down_revision = "st0pp1ng"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_attention_acks",
        sa.Column("item_id", sa.String(length=255), primary_key=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "acknowledged_by",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("dashboard_attention_acks")
