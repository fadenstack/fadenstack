"""A reply keeps the id of its provenance receipt: the signed proof this installation's model wrote it.

Revision ID: r3c31pt1d
Revises: d3l3t3d4t
Create Date: 2026-10-19 02:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "r3c31pt1d"
down_revision = "d3l3t3d4t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``receipt_id``."""
    op.add_column("chat_message", sa.Column("receipt_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("chat_message", "receipt_id")
