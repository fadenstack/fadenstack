"""Agents in the request log, and the events their SDKs report.

A request to ``/v1/agents/{agent}/...`` records the agent, the device signed
in to it, and the conversation and turn the SDK names -- the conversation
itself stays on the device. ``agent_client_event`` keeps what an SDK reports
about its work (tools run, approvals, local MCP servers), never content.

Revision ID: 4g3ntr3q5
Revises: 9n0p1q2r3s4t
Create Date: 2026-09-28 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "4g3ntr3q5"
down_revision = "9n0p1q2r3s4t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_gateway_request_log", sa.Column("agent_id", sa.String(64), nullable=True))
    op.add_column("llm_gateway_request_log", sa.Column("device_id", sa.String(64), nullable=True))
    op.add_column("llm_gateway_request_log", sa.Column("conversation_id", sa.String(128), nullable=True))
    op.add_column("llm_gateway_request_log", sa.Column("turn_id", sa.String(128), nullable=True))
    op.create_index(
        "ix_llm_gateway_request_log_agent_id", "llm_gateway_request_log", ["agent_id"],
    )

    op.create_table(
        "agent_client_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("conversation_id", sa.String(128), nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("data", postgresql.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_client_event_agent_id", "agent_client_event", ["agent_id"])
    op.create_index("ix_agent_client_event_user_id", "agent_client_event", ["user_id"])


def downgrade() -> None:
    op.drop_table("agent_client_event")
    op.drop_index("ix_llm_gateway_request_log_agent_id", table_name="llm_gateway_request_log")
    op.drop_column("llm_gateway_request_log", "turn_id")
    op.drop_column("llm_gateway_request_log", "conversation_id")
    op.drop_column("llm_gateway_request_log", "device_id")
    op.drop_column("llm_gateway_request_log", "agent_id")
