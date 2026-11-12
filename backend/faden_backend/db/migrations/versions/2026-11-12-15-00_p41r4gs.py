"""One pairing for several agents: the child table ``agent_pairing_agent``.

One row per agent a pairing asks for: its place in the request, the agent as the app named it, the agent once
the person approved, whether the approval covers it, and when the app collected its tokens. A pairing made
before this revision has no rows and works as one agent (``agent_pairings.agent_ref`` stays the first agent's).

Revision ID: p41r4gs
Revises: p41r1ng
Create Date: 2026-11-12 15:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p41r4gs"
down_revision = "p41r1ng"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_pairing_agent",
        sa.Column("pairing_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agent_pairings.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("agent_ref", sa.String(length=64), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("pairing_id", "agent_ref", name="uq_agent_pairing_agent_ref"),
    )


def downgrade() -> None:
    op.drop_table("agent_pairing_agent")
