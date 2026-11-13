"""Local skills: ``agent_versions.local_skills_mode`` (``disable`` | ``allow``).

Whether an SDK may run the person's own skills, kept on their device, for the agent (sdk/50 R3-7, R3-11).
Every version made before this revision gets ``disable``: local skills stay off until an administrator allows
them, as local MCP servers do.

Revision ID: l0c4lsk
Revises: p41r4gs
Create Date: 2026-11-13 15:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "l0c4lsk"
down_revision = "p41r4gs"
branch_labels = None
depends_on = None

_ENUM = postgresql.ENUM("disable", "allow", name="agent_local_skills_mode", create_type=False)


def upgrade() -> None:
    _ENUM.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "agent_versions",
        sa.Column("local_skills_mode", _ENUM, nullable=False, server_default="disable"),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "local_skills_mode")
    postgresql.ENUM(name="agent_local_skills_mode").drop(op.get_bind(), checkfirst=True)
