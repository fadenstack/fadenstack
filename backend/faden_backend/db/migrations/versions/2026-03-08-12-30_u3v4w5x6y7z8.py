"""Formerly: a database trigger limiting the number of remote providers.

The limit is gone. This step stays in the chain and changes nothing; installs that ran its earlier version
have the trigger dropped by revision ``n0pr0vc4p``.

Revision ID: u3v4w5x6y7z8
Revises: t2r3e4h5i6r7
Create Date: 2026-03-08 12:30:00.000000
"""

# revision identifiers, used by Alembic.
revision = "u3v4w5x6y7z8"
down_revision = "t2r3e4h5i6r7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nothing to do."""


def downgrade() -> None:
    """Nothing to undo."""
