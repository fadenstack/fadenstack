"""A deployment can be stopping: told to stop, its application not gone yet.

Revision ID: st0pp1ng
Revises: 4g3nt5
"""

from alembic import op

revision = "st0pp1ng"
down_revision = "4g3nt5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE inference_deployment_phase ADD VALUE IF NOT EXISTS 'stopping' AFTER 'degraded'")


def downgrade() -> None:
    # PostgreSQL cannot remove a value from an enum type; rows that are
    # stopping are read as stopped by an older backend's next pass anyway.
    op.execute("UPDATE inference_deployments SET phase = 'stopped' WHERE phase = 'stopping'")
