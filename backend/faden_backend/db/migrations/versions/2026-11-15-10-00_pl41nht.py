"""Plain http on the local network: ``agent_versions.local_mcp_plain_http`` (``deny`` | ``allow``).

Whether an SDK may reach a local MCP server over plain http on the local network for the agent (sdk/51 F05).
Every version made before this revision gets ``deny``: such a server is reached over https or on the person's
own computer only, until an administrator allows it.

Revision ID: pl41nht
Revises: l0c4lsk
Create Date: 2026-11-15 10:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "pl41nht"
down_revision = "l0c4lsk"
branch_labels = None
depends_on = None

_ENUM = postgresql.ENUM("deny", "allow", name="agent_local_mcp_plain_http", create_type=False)


def upgrade() -> None:
    _ENUM.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "agent_versions",
        sa.Column("local_mcp_plain_http", _ENUM, nullable=False, server_default="deny"),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "local_mcp_plain_http")
    postgresql.ENUM(name="agent_local_mcp_plain_http").drop(op.get_bind(), checkfirst=True)
