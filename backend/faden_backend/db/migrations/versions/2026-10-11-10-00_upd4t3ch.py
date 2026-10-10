"""The update check keeps the release feed it last read, and how its last attempt went.

Revision ID: upd4t3ch
Revises: t0k3nl1f3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "upd4t3ch"
down_revision = "t0k3nl1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """One row: the feed as published, its signature, and the last check."""
    op.create_table(
        "release_feed_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("index_text", sa.Text(), nullable=True),
        sa.Column("signature_text", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("etag", sa.String(256), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notified_json", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    """The check forgets what it read; the next one reads the feed again."""
    op.drop_table("release_feed_state")
