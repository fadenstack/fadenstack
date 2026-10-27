"""Outside identities waiting for an existing account's owner to confirm them.

- ``identity_link_requests``: the identity (provider, subject, its address), the account it met, the
  address the confirmation link went to, the SHA-256 of the link's token, when it expires and when it
  was used.

Revision ID: l1nkr3q
Revises: 3xt1d3nt
Create Date: 2026-10-25 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "l1nkr3q"
down_revision = "3xt1d3nt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_link_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("provider_name", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=320), nullable=False),
        sa.Column("identity_email", sa.String(length=320), nullable=True),
        sa.Column("sent_to", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_identity_link_requests_user_id", "identity_link_requests", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_identity_link_requests_user_id", table_name="identity_link_requests")
    op.drop_table("identity_link_requests")
