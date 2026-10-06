"""Every API token expires: those made to last for ever get a year from when they were made.

A new token lasts ``users.api_token_default_lifetime_s`` unless its maker names a lifetime, and at most
``users.api_token_max_lifetime_s``. The gateway reads a token's ``expires_at`` as well as its ``exp``
claim, which tokens made to last for ever do not carry.

Revision ID: t0k3nl1f3
Revises: kn0wacc3ss
"""

from __future__ import annotations

from alembic import op

revision = "t0k3nl1f3"
down_revision = "kn0wacc3ss"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Give tokens without an end the longest lifetime there is, counted from when they were made."""
    op.execute(
        "UPDATE api_tokens SET expires_at = created_at + interval '365 days' "
        "WHERE expires_at IS NULL AND revoked_at IS NULL",
    )


def downgrade() -> None:
    """Nothing to undo that can be told apart: the tokens keep the end they were given."""
