"""When a chat was deleted, so it can be erased after the organisation's retention.

Chats deleted before this migration get the time of the upgrade: none is erased sooner than the retention after
the upgrade (30 days unless the organisation sets another), whenever its person deleted it.

Revision ID: d3l3t3d4t
Revises: s34lr3st
Create Date: 2026-10-18 14:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d3l3t3d4t"
down_revision = "s34lr3st"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``deleted_at``; chats deleted before count from now."""
    op.add_column("chat_session", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_chat_session_deleted_at", "chat_session", ["deleted_at"])
    op.execute("UPDATE chat_session SET deleted_at = now() WHERE status = 'deleted' AND deleted_at IS NULL")


def downgrade() -> None:
    """Drop it again."""
    op.drop_index("ix_chat_session_deleted_at", table_name="chat_session")
    op.drop_column("chat_session", "deleted_at")
