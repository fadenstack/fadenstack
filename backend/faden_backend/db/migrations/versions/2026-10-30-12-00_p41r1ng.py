"""Signing an app in by pairing (RFC 8628): the app shows a code, a person approves it in the console.

- ``agent_pairings``: one row per pairing. The pairing code is kept as its SHA-256, the user code as an HMAC
  under a key derived from the backend's secret; the agent as the app named it, resolved at the approval;
  what the app says about itself; the requester's address; the status, who decided, and the polling interval.
  Only one pending pairing per user code. Rows go a day after they were made.
- ``agent_pairing_failures``: wrong or expired user codes tried in the console, per person and address, for
  the brute-force limits (RFC 8628 §5.1); kept ten minutes.

Revision ID: p41r1ng
Revises: tk3nus3
Create Date: 2026-10-30 12:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p41r1ng"
down_revision = "tk3nus3"
branch_labels = None
depends_on = None

_STATUS = postgresql.ENUM(
    "pending", "approved", "denied", "consumed", "expired", name="agent_pairing_status", create_type=False,
)


def upgrade() -> None:
    _STATUS.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "agent_pairings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("agent_ref", sa.String(length=64), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("pairing_code_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("user_code_hmac", sa.String(length=64), nullable=False),
        sa.Column("device_label", sa.String(length=200), nullable=False),
        sa.Column("client_name", sa.String(length=100), nullable=True),
        sa.Column("client_version", sa.String(length=50), nullable=True),
        sa.Column("client_os", sa.String(length=100), nullable=True),
        sa.Column("requester_ip", postgresql.INET(), nullable=True),
        sa.Column("status", _STATUS, nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("failed_polls", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "uq_agent_pairings_pending_user_code", "agent_pairings", ["user_code_hmac"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_agent_pairings_user_code_hmac", "agent_pairings", ["user_code_hmac"])
    op.create_index("ix_agent_pairings_requester_ip_created_at", "agent_pairings", ["requester_ip", "created_at"])
    op.create_index("ix_agent_pairings_created_at", "agent_pairings", ["created_at"])

    op.create_table(
        "agent_pairing_failures",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_pairing_failures_user_at", "agent_pairing_failures", ["user_id", "failed_at"])
    op.create_index("ix_agent_pairing_failures_ip_at", "agent_pairing_failures", ["ip", "failed_at"])


def downgrade() -> None:
    op.drop_table("agent_pairing_failures")
    op.drop_table("agent_pairings")
    _STATUS.drop(op.get_bind(), checkfirst=True)
