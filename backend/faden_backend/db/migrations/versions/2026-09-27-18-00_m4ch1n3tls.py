"""The machine CA: TLS between the gateway and the models on the machines.

Revision ID: m4ch1n3tls
Revises: r3s1d3nc3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m4ch1n3tls"
down_revision = "r3s1d3nc3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_runtimes",
        sa.Column("machine_tls", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "machine_ca",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("key_sealed", sa.Text(), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issuing_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=False, server_default="first"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "machine_tls_client",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("key_sealed", sa.Text(), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ca_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("machine_ca.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "machine_certificate",
        sa.Column(
            "node_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("infra_node.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("serial", sa.String(length=64), nullable=False),
        sa.Column("names_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ca_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("machine_ca.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("machine_certificate")
    op.drop_table("machine_tls_client")
    op.drop_table("machine_ca")
    op.drop_column("llm_runtimes", "machine_tls")
