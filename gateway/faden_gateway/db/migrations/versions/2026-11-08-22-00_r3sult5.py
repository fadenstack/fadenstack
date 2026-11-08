"""What a covered call's result was, who changed a grant, and calls found by their time (sdk/40 A4.0).

``approval_grant_call`` gains when the gateway saw the call's result (``result_at``: after running it, or in the
device's next request; sdk/09 L-61) and, for a grant that records results, its ``outcome`` and the start of the
result after the PII masking (``result``, encrypted as the arguments; L-60). ``approval_grant_change`` says who
changed a grant (``changed_by``: ``device`` or ``limit``). An index on the calls' time serves searches by a time
window.

Revision ID: r3sult5
Revises: byp4ss
Create Date: 2026-11-08 22:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "r3sult5"
down_revision = "byp4ss"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the columns and the index."""
    op.add_column("approval_grant_call", sa.Column("result_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("approval_grant_call", sa.Column("outcome", sa.String(8), nullable=True))
    op.add_column("approval_grant_call", sa.Column("result", sa.Text(), nullable=True))
    op.add_column("approval_grant_change", sa.Column("changed_by", sa.String(16), nullable=True))
    op.create_index("ix_approval_grant_call_created_at", "approval_grant_call", ["created_at"])


def downgrade() -> None:
    """Drop them again."""
    op.drop_index("ix_approval_grant_call_created_at", table_name="approval_grant_call")
    op.drop_column("approval_grant_change", "changed_by")
    op.drop_column("approval_grant_call", "result")
    op.drop_column("approval_grant_call", "outcome")
    op.drop_column("approval_grant_call", "result_at")
