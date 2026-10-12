"""Console sign-ins can be ended: a logout, a password change, a suspension.

``user.sessions_valid_after`` ends every sign-in made before it; ``revoked_sign_ins`` keeps the ``jti`` of each
one a logout ended until it would have expired. Sign-ins made before this carry no ``jti`` and are refused: their
owners sign in again.

Revision ID: s1gn1nr3v
Revises: pr0vk3ys
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "s1gn1nr3v"
down_revision = "pr0vk3ys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the column and the table."""
    op.add_column("user", sa.Column("sessions_valid_after", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "revoked_sign_ins",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_revoked_sign_ins_user_id", "revoked_sign_ins", ["user_id"])


def downgrade() -> None:
    """Drop them again."""
    op.drop_index("ix_revoked_sign_ins_user_id", table_name="revoked_sign_ins")
    op.drop_table("revoked_sign_ins")
    op.drop_column("user", "sessions_valid_after")
