"""A Ray cluster's own CA, for Ray's mutual TLS between its machines.

Revision ID: r4yt1sca
Revises: m4ch1n3tls
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r4yt1sca"
down_revision = "m4ch1n3tls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ray_cluster_ca",
        sa.Column(
            "environment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inference_environments.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("key_sealed", sa.Text(), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ray_cluster_ca")
