"""Whether an account has a password someone knows.

- ``user.password_set``: false for an account a sign-in provider or a provisioning source made (its password is
  random and nobody knows it) until its owner sets one. Accounts a source provisioned are marked here; every
  other existing account counts as having one.

Revision ID: pwds3t
Revises: pr0v1s10n
Create Date: 2026-10-26 20:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "pwds3t"
down_revision = "pr0v1s10n"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("password_set", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute('UPDATE "user" SET password_set = false WHERE id IN (SELECT user_id FROM provisioned_accounts)')


def downgrade() -> None:
    op.drop_column("user", "password_set")
