"""Linking proved in the browser that signed in; mail that expires.

- ``identity_link_requests``: the identity as the module gave it, the tries, the last code sent (an HMAC),
  how many codes and when. Requests made before (their links went by mail) are deleted: they last ten
  minutes and the new routes do not take them.
- ``notification_outbox.expires_at``: a message not sent by then is deleted. Rows from before get an hour
  for a reset or a link, a day for anything else; unsent link mails are deleted (no sender takes them).

Revision ID: l1nkpr0f
Revises: l1nkr3q
Create Date: 2026-10-25 18:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "l1nkpr0f"
down_revision = "l1nkr3q"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM identity_link_requests")
    op.add_column("identity_link_requests", sa.Column("identity_json", postgresql.JSONB(), nullable=False))
    op.add_column("identity_link_requests",
                  sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("identity_link_requests", sa.Column("code_hash", sa.String(length=64), nullable=True))
    op.add_column("identity_link_requests",
                  sa.Column("codes_sent", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("identity_link_requests", sa.Column("code_sent_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("DELETE FROM notification_outbox WHERE kind = 'link_confirmation' AND status <> 'sent'")
    op.add_column("notification_outbox", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE notification_outbox SET expires_at = created_at + CASE "
        "WHEN kind IN ('password_reset', 'link_confirmation') THEN interval '1 hour' "
        "ELSE interval '1 day' END",
    )
    op.alter_column("notification_outbox", "expires_at", nullable=False)
    op.create_index("ix_notification_outbox_expires_at", "notification_outbox", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_expires_at", table_name="notification_outbox")
    op.drop_column("notification_outbox", "expires_at")
    op.execute("DELETE FROM identity_link_requests")
    for column in ("code_sent_at", "codes_sent", "code_hash", "attempts", "identity_json"):
        op.drop_column("identity_link_requests", column)
