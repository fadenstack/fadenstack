"""Restrictions an extension keeps on a whole tenant.

- ``tenant_restrictions``: one row per restriction name an extension keeps on a tenant; while nothing
  installed enforces it, core fails closed in the way it knows for that name.

Revision ID: t3n4ntr
Revises: r3str1ct
Create Date: 2026-10-26 09:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "t3n4ntr"
down_revision = "r3str1ct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_restrictions",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "name"),
    )


def downgrade() -> None:
    op.drop_table("tenant_restrictions")
