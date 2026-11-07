"""Extensions of a running grant (``approval_grant_change``).

The agent may ask to extend a grant -- more time, another place -- and the person allows it on the same card. The
grant grows in place, never past its longest span from when it was issued, and each extension is kept here with
what the card showed (sdk/40 A2.2).

Revision ID: gr4ntx
Revises: gr4nts
Create Date: 2026-11-07 20:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "gr4ntx"
down_revision = "gr4nts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the table."""
    op.create_table(
        "approval_grant_change",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "grant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approval_grant.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("added_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_targets", sa.Text(), nullable=True),
        sa.Column("expires_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shown_text_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_approval_grant_change_grant_id", "approval_grant_change", ["grant_id"])


def downgrade() -> None:
    """Drop it again."""
    op.drop_index("ix_approval_grant_change_grant_id", table_name="approval_grant_change")
    op.drop_table("approval_grant_change")
