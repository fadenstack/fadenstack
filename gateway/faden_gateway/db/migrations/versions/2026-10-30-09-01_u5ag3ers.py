"""The people erased lately (``usage_erased``), whose usage every rollup run removes again for 30 days.

Erasing a person (an account deleted, M12) deletes their request-log rows and person-level rollups at once;
a rollup run that read the log just before, or a request of theirs that ended just after, could write them
again. Each run deletes what of theirs turns up and writes no person row for them, as long as their row
here is younger than 30 days.

Revision ID: u5ag3ers
Revises: u5ag3rfsd
Create Date: 2026-10-30 09:01:00
"""

import sqlalchemy as sa
from alembic import op

revision = "u5ag3ers"
down_revision = "u5ag3rfsd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``usage_erased``."""
    op.create_table(
        "usage_erased",
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop it again."""
    op.drop_table("usage_erased")
