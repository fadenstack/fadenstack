"""Chat projects have no limit: drop the trigger that capped them.

Installs that ran ``3c4d5e6f7a8b`` before its trigger was taken out carry one refusing a fourth chat project per
person. This drops the trigger and its function where they exist.

Revision ID: n0pr0jc4p
Revises: p11w34k3n
"""

from __future__ import annotations

from alembic import op

revision = "n0pr0jc4p"
down_revision = "p11w34k3n"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the project trigger and its function, if present."""
    op.execute("DROP TRIGGER IF EXISTS trg_enforce_chat_project_limit ON chat_project")
    op.execute("DROP FUNCTION IF EXISTS enforce_chat_project_limit()")


def downgrade() -> None:
    """Nothing to restore: the limit does not come back."""
