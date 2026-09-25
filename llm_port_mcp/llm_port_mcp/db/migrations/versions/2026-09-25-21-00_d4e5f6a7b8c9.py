"""Add last_error to mcp_servers.

A server whose connect or discovery failed was set to "error" -- or, when the
failure surfaced as a cancellation, left "registering" -- with no record of
why. The reason is kept now, and cleared when a connect succeeds.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-25 21:00:00

"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the column."""
    op.add_column("mcp_servers", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the column."""
    op.drop_column("mcp_servers", "last_error")
