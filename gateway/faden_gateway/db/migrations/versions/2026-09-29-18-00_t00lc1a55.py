"""Tool classes and tool modes: the admin's classification rules, a chat's tool mode.

Revision ID: t00lc1a55
Revises: g0v3rn4nc3
Create Date: 2026-09-29 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "t00lc1a55"
down_revision = "g0v3rn4nc3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the table and the column."""
    op.add_column("session_execution_policy", sa.Column("tool_mode", sa.String(16), nullable=True))
    op.create_table(
        "llm_gateway_tool_classification",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("tool_pattern", sa.String(256), nullable=False),
        sa.Column("tool_class", sa.String(16), nullable=False),
        sa.Column("open_world", sa.Boolean(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_llm_gateway_tool_classification_tenant_id", "llm_gateway_tool_classification", ["tenant_id"],
    )


def downgrade() -> None:
    """Drop them again."""
    op.drop_table("llm_gateway_tool_classification")
    op.drop_column("session_execution_policy", "tool_mode")
