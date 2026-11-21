"""0.7.2: the request log counts the pictures the PII scan passed on unchecked (G); a chat's summary says how
it was written (K).

- ``llm_gateway_request_log.pii_pictures_unchecked``: how many pictures a request carried that the PII
  scan passed on as they came (it reads text only), so the record of a turn says a picture went unchecked;
  their count only, never what they show. Rows made before keep it empty.
- ``session_summary.model_alias`` and ``record_id``: the model that wrote a chat's rolling summary, and the
  provenance engine's record of it (edit kind ``summarised``), so the summary is shown as AI-written.

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
    """Add the columns."""
    op.add_column("llm_gateway_request_log", sa.Column("pii_pictures_unchecked", sa.Integer(), nullable=True))
    op.add_column("session_summary", sa.Column("model_alias", sa.String(256), nullable=True))
    op.add_column("session_summary", sa.Column("record_id", sa.String(128), nullable=True))


def downgrade() -> None:
    """Drop them again."""
    op.drop_column("session_summary", "record_id")
    op.drop_column("session_summary", "model_alias")
    op.drop_column("llm_gateway_request_log", "pii_pictures_unchecked")
