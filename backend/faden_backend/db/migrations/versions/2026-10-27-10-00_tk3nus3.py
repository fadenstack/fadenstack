"""Every use of an API token.

- ``api_token_uses``: one row per request the gateway let in with an API token: the token, its owner, when, the
  caller's address, the client's name and the route (``db/models/api_tokens.py``). Deleted after
  ``api_token_use_retention_days`` by the sign-in upkeep.

Revision ID: tk3nus3
Revises: 4cc3ss
Create Date: 2026-10-27 10:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "tk3nus3"
down_revision = "4cc3ss"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_token_uses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("token_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("api_tokens.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("address", sa.String(length=64), nullable=True),
        sa.Column("client", sa.String(length=200), nullable=True),
        sa.Column("route", sa.String(length=200), nullable=True),
    )
    op.create_index("ix_api_token_uses_token_at", "api_token_uses", ["token_id", "at"])
    op.create_index("ix_api_token_uses_user_at", "api_token_uses", ["user_id", "at"])
    op.create_index("ix_api_token_uses_at", "api_token_uses", ["at"])


def downgrade() -> None:
    op.drop_table("api_token_uses")
