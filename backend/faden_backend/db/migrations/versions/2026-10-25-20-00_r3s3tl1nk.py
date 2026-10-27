"""One-time password reset links an administrator creates.

- ``password_reset_links``: the account, the administrator who made it, the SHA-256 of the link's token, when
  it expires and when it was used.

Revision ID: r3s3tl1nk
Revises: l1nkpr0f
Create Date: 2026-10-25 20:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r3s3tl1nk"
down_revision = "l1nkpr0f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_password_reset_links_user_id", "password_reset_links", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_links_user_id", table_name="password_reset_links")
    op.drop_table("password_reset_links")
