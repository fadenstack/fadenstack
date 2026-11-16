"""Section 8's server parts (sdk/51): each device's own report, and the arguments' hash on decision records.

- ``agent_device_inventory``: what a device signed in to an agent last reported about itself (the manifest's
  ``device`` block: the system, the app's version, its plugins and local MCP programs with their SHA-256), one row
  per agent and device, shown per device in the console (8.3).
- ``llm_gateway_policy_decision.arguments_sha256``: the SHA-256 of a call's arguments as the answering client
  hashed them, so the record matches the device's audit log (8.10). Records made before keep it empty.

Revision ID: d3v1c3s
Revises: st3pup
Create Date: 2026-11-16 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d3v1c3s"
down_revision = "st3pup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the table and the column."""
    op.create_table(
        "agent_device_inventory",
        sa.Column("agent_id", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("os", sa.String(100), nullable=True),
        sa.Column("app_version", sa.String(50), nullable=True),
        sa.Column("plugins", sa.JSON(), nullable=False),
        sa.Column("mcp_programs", sa.JSON(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_device_inventory_device_id", "agent_device_inventory", ["device_id"])
    op.create_index("ix_agent_device_inventory_user_id", "agent_device_inventory", ["user_id"])
    op.create_index("ix_agent_device_inventory_reported_at", "agent_device_inventory", ["reported_at"])
    op.add_column("llm_gateway_policy_decision", sa.Column("arguments_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    """Drop them again."""
    op.drop_column("llm_gateway_policy_decision", "arguments_sha256")
    op.drop_index("ix_agent_device_inventory_reported_at", table_name="agent_device_inventory")
    op.drop_index("ix_agent_device_inventory_user_id", table_name="agent_device_inventory")
    op.drop_index("ix_agent_device_inventory_device_id", table_name="agent_device_inventory")
    op.drop_table("agent_device_inventory")
