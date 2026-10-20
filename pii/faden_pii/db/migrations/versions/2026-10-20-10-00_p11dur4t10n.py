"""Add duration_ms column to pii_scan_events.

Revision ID: p11dur4t10n
Revises: a1b2c3d4e5f6
Create Date: 2026-10-20 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "p11dur4t10n"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable duration_ms column to pii_scan_events."""
    op.add_column(
        "pii_scan_events",
        sa.Column("duration_ms", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Drop duration_ms column from pii_scan_events."""
    op.drop_column("pii_scan_events", "duration_ms")

