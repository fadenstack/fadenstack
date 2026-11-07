"""Each agent version's approval levels (``agent_versions.approvals``, sdk/38 §5).

``{default_level, max_level}``: the tool mode a conversation of the agent starts in, and the most it may use.
Every existing version gets ``ask`` up to ``auto``, which is how agents behaved until now.

Revision ID: 4ppr0v4l5
Revises: u6t3amtr33
Create Date: 2026-11-07 14:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "4ppr0v4l5"
down_revision = "u6t3amtr33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``approvals``, Ask up to Auto for every version there is."""
    op.add_column(
        "agent_versions",
        sa.Column(
            "approvals", postgresql.JSONB(), nullable=False,
            server_default=sa.text("""'{"default_level": "ask", "max_level": "auto"}'::jsonb"""),
        ),
    )


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("agent_versions", "approvals")
