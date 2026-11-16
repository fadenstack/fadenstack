"""An agent's off-switch (sdk/51 8.3), and access tokens of 300 s at most (8.11).

``agents.turned_off_at`` and ``agents.turned_off_by``: while ``turned_off_at`` is set, the gateway refuses
the agent's apps with ``403 agent_disabled`` and its profile says ``enabled: false``; sign-ins stay. Every agent
is on after this revision. ``agents`` already moves the access count on any change (``db/access_epoch.py``), so
the gateway sees the switch at the next request.

An agent access token lives 300 s at most from this version on. A larger lifetime an administrator stored
(``agents.access_token_ttl_s``) becomes 300, so the console shows what tokens get; going back does not restore
it.

Revision ID: 0ffsw1t
Revises: pl41nht
Create Date: 2026-11-16 18:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0ffsw1t"
down_revision = "pl41nht"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the columns, every agent staying on; cut a stored access-token lifetime to 300 s."""
    op.add_column("agents", sa.Column("turned_off_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "agents",
        sa.Column(
            "turned_off_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL", name="fk_agents_turned_off_by"),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE system_setting_value
        SET value_json = jsonb_set(value_json, '{value}', '300'::jsonb)
        WHERE key = 'agents.access_token_ttl_s'
          AND jsonb_typeof(value_json -> 'value') = 'number'
          AND (value_json ->> 'value')::numeric > 300
        """,
    )


def downgrade() -> None:
    """Drop them again (every agent on)."""
    op.drop_column("agents", "turned_off_by")
    op.drop_column("agents", "turned_off_at")
