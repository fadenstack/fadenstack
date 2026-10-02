"""A download job's heartbeat, the run that holds it, and why it stopped.

Revision ID: dl0adh3art
Revises: r4yt1sca
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dl0adh3art"
down_revision = "r4yt1sca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("download_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("download_jobs", sa.Column("runner_id", sa.String(length=64), nullable=True))
    op.add_column("download_jobs", sa.Column("error_code", sa.String(length=32), nullable=True))
    # Downloads the web backend's start failed before there were heartbeats:
    # interrupted, not failed -- nothing was wrong with them. Said so, a
    # deployment waiting for the model resumes one by itself.
    op.execute(
        "UPDATE download_jobs SET error_code = 'interrupted' "
        "WHERE status = 'failed' AND error_message LIKE 'Interrupted by server restart%'"
    )


def downgrade() -> None:
    op.drop_column("download_jobs", "error_code")
    op.drop_column("download_jobs", "runner_id")
    op.drop_column("download_jobs", "heartbeat_at")
