"""The second sign-in before Bypass is switched on: step-ups and their failed tries (sdk/41).

``bypass_step_ups`` holds each step-up an app starts, shaped as a device pairing (RFC 8628): the app's code as a
hash, the link's code as an HMAC, the browser that opened it, and how and when the person signed in again.
``bypass_step_up_failures`` counts wrong codes and wrong sign-ins per person and address for the limits.

Revision ID: st3pup0
Revises: 4ppr0v4l5
Create Date: 2026-11-09 18:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "st3pup0"
down_revision = "4ppr0v4l5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the two tables."""
    op.create_table(
        "bypass_step_ups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_label", sa.String(200), nullable=True),
        sa.Column("client_version", sa.String(64), nullable=True),
        sa.Column("longest_minutes", sa.Integer(), nullable=True),
        sa.Column("step_up_code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_code_hmac", sa.String(64), nullable=False),
        sa.Column("browser_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requester_ip", postgresql.INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_s", sa.Integer(), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("method", sa.String(16), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("idp_auth_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sso_tx_hash", sa.String(64), nullable=True),
        sa.Column("sso_asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_sign_ins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_bypass_step_ups_user_id", "bypass_step_ups", ["user_id"])
    op.create_index("ix_bypass_step_ups_device_id", "bypass_step_ups", ["device_id"])
    op.create_index("ix_bypass_step_ups_user_code_hmac", "bypass_step_ups", ["user_code_hmac"])
    op.create_index("ix_bypass_step_ups_sso_tx_hash", "bypass_step_ups", ["sso_tx_hash"])
    op.create_table(
        "bypass_step_up_failures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bypass_step_up_failures_user_id", "bypass_step_up_failures", ["user_id"])
    op.create_index("ix_bypass_step_up_failures_failed_at", "bypass_step_up_failures", ["failed_at"])


def downgrade() -> None:
    """Drop them again."""
    op.drop_table("bypass_step_up_failures")
    op.drop_table("bypass_step_ups")
