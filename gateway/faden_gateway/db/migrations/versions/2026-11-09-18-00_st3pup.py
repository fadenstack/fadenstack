"""How the person signed in again to switch Bypass on (sdk/41; sdk/09 L-59).

``approval_grant`` gains the second sign-in a Bypass grant was made with: its method (``password``, ``oidc``,
``saml``, ``ldap``), the provider's key, when it happened and the backend's step-up it came from. Grants made
before keep them empty.

Revision ID: st3pup
Revises: r3sult5
Create Date: 2026-11-09 18:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "st3pup"
down_revision = "r3sult5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the columns."""
    op.add_column("approval_grant", sa.Column("step_up_method", sa.String(16), nullable=True))
    op.add_column("approval_grant", sa.Column("step_up_provider", sa.String(64), nullable=True))
    op.add_column("approval_grant", sa.Column("step_up_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("approval_grant", sa.Column("step_up_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    """Drop them again."""
    op.drop_column("approval_grant", "step_up_id")
    op.drop_column("approval_grant", "step_up_at")
    op.drop_column("approval_grant", "step_up_provider")
    op.drop_column("approval_grant", "step_up_method")
