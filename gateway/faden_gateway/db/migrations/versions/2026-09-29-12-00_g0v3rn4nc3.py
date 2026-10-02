"""Governance: decision records, the tenant's HMAC key, tool rules, blocked turns.

A governance check that objects leaves a decision record -- stage, outcome,
policy, category and an HMAC of the text, never the text. ``chat_message``
gets the decision and that HMAC: a blocked turn (status ``blocked_input`` or
``blocked_output``, the column 9n0p1q2r3s4t added) stays in the history the
user sees and out of what the model is sent.

Revision ID: g0v3rn4nc3
Revises: 4g3ntr3q5
Create Date: 2026-09-29 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "g0v3rn4nc3"
down_revision = "4g3ntr3q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the tables and columns."""
    op.add_column("chat_message", sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("chat_message", sa.Column("content_hmac", sa.String(64), nullable=True))

    op.create_table(
        "llm_gateway_policy_decision",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("policy_name", sa.String(256), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("check", sa.String(128), nullable=False),
        sa.Column("content_hmac", sa.String(64), nullable=True),
        sa.Column("tool_id", sa.String(256), nullable=True),
        sa.Column("reasons", postgresql.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_llm_gateway_policy_decision_tenant_id", "llm_gateway_policy_decision", ["tenant_id"])
    op.create_index("ix_llm_gateway_policy_decision_user_id", "llm_gateway_policy_decision", ["user_id"])
    op.create_index("ix_llm_gateway_policy_decision_created_at", "llm_gateway_policy_decision", ["created_at"])

    op.create_table(
        "llm_gateway_governance_key",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "llm_gateway_tool_rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("tool_pattern", sa.String(256), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_llm_gateway_tool_rule_tenant_id", "llm_gateway_tool_rule", ["tenant_id"])


def downgrade() -> None:
    """Drop them again."""
    op.drop_table("llm_gateway_tool_rule")
    op.drop_table("llm_gateway_governance_key")
    op.drop_table("llm_gateway_policy_decision")
    op.drop_column("chat_message", "content_hmac")
    op.drop_column("chat_message", "decision_id")
