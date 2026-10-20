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
    """Accept the compatible column already installed by an early canary."""
    if op.get_context().as_sql:
        op.execute(
            "ALTER TABLE pii_scan_events ADD COLUMN IF NOT EXISTS duration_ms DOUBLE PRECISION"
        )
        return
    columns = sa.inspect(op.get_bind()).get_columns("pii_scan_events")
    existing = next((c for c in columns if c["name"] == "duration_ms"), None)
    if existing is not None:
        if not isinstance(existing["type"], sa.Float) or not existing["nullable"]:
            raise RuntimeError(
                "Existing pii_scan_events.duration_ms has an incompatible schema"
            )
        return
    op.add_column(
        "pii_scan_events",
        sa.Column("duration_ms", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Drop duration_ms column from pii_scan_events."""
    op.drop_column("pii_scan_events", "duration_ms")
