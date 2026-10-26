"""Accounts and teams an outside source keeps.

- ``provisioned_accounts``: the source that made or took over an account, its ID and login name for the
  person, whether the account's suspension was the source's, and when the source removed the person.
- ``groups.source``, ``groups.external_id``: who keeps a team (``None``: an administrator) and the source's ID
  for it; one per source and ID.

Revision ID: pr0v1s10n
Revises: t3n4ntr
Create Date: 2026-10-26 14:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "pr0v1s10n"
down_revision = "t3n4ntr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provisioned_accounts",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("login", sa.String(length=320), nullable=False),
        sa.Column("suspended_by_source", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_provisioned_accounts_source_external_id"),
    )
    op.create_index(
        "ux_provisioned_accounts_source_login", "provisioned_accounts", ["source", sa.text("lower(login)")],
        unique=True,
    )
    op.add_column("groups", sa.Column("source", sa.String(length=80), nullable=True))
    op.add_column("groups", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.create_index(
        "ux_groups_source_external_id", "groups", ["source", "external_id"], unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_groups_source_external_id", table_name="groups")
    op.drop_column("groups", "external_id")
    op.drop_column("groups", "source")
    op.drop_index("ux_provisioned_accounts_source_login", table_name="provisioned_accounts")
    op.drop_table("provisioned_accounts")
