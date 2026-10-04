"""What the apps that run an agent bring: their manifests, for review.

An SDK reports its app's tools (with the classes their developer gave them),
context sources and defaults when it starts; tools the gateway held back from
a request, because the agent's contract does not list them, are kept the same
way. One row per distinct manifest of an agent, counted as it comes again.

Revision ID: m4n1f35t
Revises: t00lc1a55
Create Date: 2026-10-04 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "m4n1f35t"
down_revision = "t00lc1a55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_client_manifest",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("app_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("app_version", sa.String(50), nullable=False, server_default=""),
        sa.Column("manifest", postgresql.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reports", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_user_id", sa.String(128), nullable=False),
        sa.Column("last_device_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("agent_id", "sha256", name="uq_agent_client_manifest_sha"),
    )
    op.create_index("ix_agent_client_manifest_agent_id", "agent_client_manifest", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_client_manifest_agent_id", table_name="agent_client_manifest")
    op.drop_table("agent_client_manifest")
