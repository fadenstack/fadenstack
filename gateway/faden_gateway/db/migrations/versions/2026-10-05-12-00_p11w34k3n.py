"""A chat's PII override keeps whether it was saved with the right to weaken.

An admin (``pii.session:weaken``, or a superuser) may set a chat's PII policy
below the tenant's, switching the egress mode among it. The panel used that
right, but the chat's requests never did: the panel said tokenize, the turn
was redacted. The right is now stored with the override when it is saved, from
the backend's signed grant, and the chat's requests use it.

An override saved before this counts as saved without the right (``false``).

Revision ID: p11w34k3n
Revises: m4n1f35t
Create Date: 2026-10-05 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "p11w34k3n"
down_revision = "m4n1f35t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_pii_override",
        sa.Column("allow_weaken", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("session_pii_override", "allow_weaken")
