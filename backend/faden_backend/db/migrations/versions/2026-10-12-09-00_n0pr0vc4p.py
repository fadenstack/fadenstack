"""Remote providers have no limit: drop the trigger that capped them.

Installs that ran ``u3v4w5x6y7z8`` before it was emptied carry a trigger refusing a fourth remote provider.
This drops the trigger and its function where they exist.

Revision ID: n0pr0vc4p
Revises: upd4t3ch
"""

from __future__ import annotations

from alembic import op

revision = "n0pr0vc4p"
down_revision = "upd4t3ch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the provider trigger and its function, if present."""
    op.execute("DROP TRIGGER IF EXISTS trg_enforce_remote_provider_limit ON llm_providers")
    op.execute("DROP FUNCTION IF EXISTS enforce_remote_provider_limit()")


def downgrade() -> None:
    """Nothing to restore: the limit does not come back."""
