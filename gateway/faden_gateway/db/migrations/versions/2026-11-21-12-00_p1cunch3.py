"""0.7.2 G: the request log counts the pictures the PII scan passed on unchecked.

- ``llm_gateway_request_log.pii_pictures_unchecked``: how many pictures a request carried that the PII
  scan passed on as they came (it reads text only), so the record of a turn says a picture went unchecked;
  their count only, never what they show. Rows made before keep it empty.

Revision ID: p1cunch3
Revises: d3v1c3s
Create Date: 2026-11-21 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "p1cunch3"
down_revision = "d3v1c3s"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the column."""
    op.add_column("llm_gateway_request_log", sa.Column("pii_pictures_unchecked", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("llm_gateway_request_log", "pii_pictures_unchecked")
