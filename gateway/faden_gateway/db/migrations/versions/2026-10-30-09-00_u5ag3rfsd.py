"""The request log says whether a cap or an admission check refused the request (``refused``).

The gateway sets it on every row it writes: true for a request one of the tenant's caps or an extension's
admission check refused, false otherwise. Rows written before stay NULL; the rollup job counts those by
their error code, as it did.

Revision ID: u5ag3rfsd
Revises: u5ag3l1m
Create Date: 2026-10-30 09:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "u5ag3rfsd"
down_revision = "u5ag3l1m"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``refused``."""
    op.add_column("llm_gateway_request_log", sa.Column("refused", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("llm_gateway_request_log", "refused")
