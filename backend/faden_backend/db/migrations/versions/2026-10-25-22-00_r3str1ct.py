"""Restrictions an extension keeps on an agent beyond its grants.

- ``agents.restricted_by``: their names (an extension sets them); one no installed extension enforces closes the
  agent to all but its maintainers.

Revision ID: r3str1ct
Revises: r3s3tl1nk
Create Date: 2026-10-25 22:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r3str1ct"
down_revision = "r3s3tl1nk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("restricted_by", postgresql.ARRAY(sa.String(length=64)), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("agents", "restricted_by")
