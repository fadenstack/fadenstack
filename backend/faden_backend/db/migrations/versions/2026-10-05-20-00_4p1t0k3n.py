"""API tokens get a row each, so one can be revoked without signing everyone out.

The gateway looks a token up by its ``jti`` (the row's id). Tokens made before
this have no row: the gateway refuses them, and their owners make new ones.

Revision ID: 4p1t0k3n
Revises: 4g3ntp11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "4p1t0k3n"
down_revision = "4g3ntp11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``api_tokens``."""
    op.create_table(
        "api_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default="default"),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])


def downgrade() -> None:
    """Drop ``api_tokens``."""
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
