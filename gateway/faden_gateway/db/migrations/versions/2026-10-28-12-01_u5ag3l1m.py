"""A tenant's cap on tokens per day, beside its requests and tokens per minute.

A calendar day in the usage time zone (``usage.time_zone``); NULL: no cap.

Revision ID: u5ag3l1m
Revises: u5ag3r0ll
Create Date: 2026-10-28 12:01:00
"""

import sqlalchemy as sa
from alembic import op

revision = "u5ag3l1m"
down_revision = "u5ag3r0ll"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``tokens_per_day``."""
    op.add_column("tenant_llm_policy", sa.Column("tokens_per_day", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("tenant_llm_policy", "tokens_per_day")
