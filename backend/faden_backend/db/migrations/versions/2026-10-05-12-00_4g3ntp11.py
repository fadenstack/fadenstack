"""An agent's version holds its PII settings: whether its conversations may switch the PII mode.

``{"allow_mode_override": true | false | null}``; null, and every version from
before, follows the tenant's setting (``tenant_llm_policy.allow_mode_override``
in the gateway's database). The gateway reads it with the rest of the version.

Revision ID: 4g3ntp11
Revises: 4ckn0wl3dg3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "4g3ntp11"
down_revision = "4ckn0wl3dg3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_versions",
        sa.Column("pii", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "pii")
