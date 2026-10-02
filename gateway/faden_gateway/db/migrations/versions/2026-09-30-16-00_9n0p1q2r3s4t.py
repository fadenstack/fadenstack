"""A chat reply is a turn with a status, an error and a heartbeat.

The reply's row is written when it starts (``running``) and ends ``complete``,
``failed``, ``stopped`` or ``interrupted``. A failure was never stored: the
chat page only guessed one from a question left without an answer, and the
error itself was lost with the browser tab that saw it. Existing rows are
complete. ``error`` is encrypted like the content it may quote.

Revision ID: 9n0p1q2r3s4t
Revises: 8m9n0p1q2r3s
Create Date: 2026-09-30 16:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "9n0p1q2r3s4t"
down_revision = "8m9n0p1q2r3s"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_message",
        sa.Column("status", sa.String(16), nullable=False, server_default="complete"),
    )
    op.add_column("chat_message", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("chat_message", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_chat_message_running",
        "chat_message",
        ["updated_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_message_running", table_name="chat_message")
    op.drop_column("chat_message", "updated_at")
    op.drop_column("chat_message", "error")
    op.drop_column("chat_message", "status")
