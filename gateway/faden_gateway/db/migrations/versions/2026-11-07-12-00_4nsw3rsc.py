"""What an approval said it covers (``llm_gateway_policy_decision.answer_scope``).

The user's answer to a call that waited names its scope -- ``once``, ``chat`` or ``always`` -- and the
decision record keeps it. Older rows, and answers from clients that do not say, have none.

Revision ID: 4nsw3rsc
Revises: u5ag3ers
Create Date: 2026-11-07 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "4nsw3rsc"
down_revision = "u5ag3ers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``answer_scope``."""
    op.add_column("llm_gateway_policy_decision", sa.Column("answer_scope", sa.String(16), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("llm_gateway_policy_decision", "answer_scope")
