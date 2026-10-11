"""An API token's tenant is its account's: tokens made with another tenant are put in ``default``.

The tenant a token was made with came from whoever made it. Accounts are in the ``default`` tenant
(console sign-ins carry no other), new tokens are made there, and the gateway now takes a token's
tenant from this row rather than from the token. Tokens keep working, under the rules of the tenant
their account is in.

Revision ID: t0k3nt3n4nt
Revises: t0k3nl1f3
"""

from __future__ import annotations

from alembic import op

revision = "t0k3nt3n4nt"
down_revision = "t0k3nl1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Put every token in the accounts' tenant."""
    op.execute("UPDATE api_tokens SET tenant_id = 'default' WHERE tenant_id <> 'default'")


def downgrade() -> None:
    """Nothing to undo: which tenant a token was made with is not kept."""
