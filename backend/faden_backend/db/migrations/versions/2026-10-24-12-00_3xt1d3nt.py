"""One link per person and provider; when they last signed in that way; who keeps a group membership.

- ``oauth_account``: a person (``oauth_name`` = provider key, ``account_id`` = their ID there) has one row.
  Rows without an ID name nobody and go; of duplicates the one with the highest ``id`` stays.
  ``last_sign_in_at`` is new.
- ``user_groups.source``: ``NULL`` for a membership an administrator set (all existing ones), or the
  sign-in provider that keeps it.

Revision ID: 3xt1d3nt
Revises: t3xtm4rk
Create Date: 2026-10-24 12:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "3xt1d3nt"
down_revision = "t3xtm4rk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM oauth_account WHERE account_id = ''")
    op.execute(
        "DELETE FROM oauth_account a USING oauth_account b "
        "WHERE a.oauth_name = b.oauth_name AND a.account_id = b.account_id AND a.id < b.id",
    )
    op.create_unique_constraint(
        "uq_oauth_account_provider_subject", "oauth_account", ["oauth_name", "account_id"],
    )
    op.add_column("oauth_account", sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_groups", sa.Column("source", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("user_groups", "source")
    op.drop_column("oauth_account", "last_sign_in_at")
    op.drop_constraint("uq_oauth_account_provider_subject", "oauth_account", type_="unique")
