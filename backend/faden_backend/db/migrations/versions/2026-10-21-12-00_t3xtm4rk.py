"""What an administrator set about marking a provider's chat answers: llm_providers.text_marking.

With the provenance module on, the gateway refuses a chat to a route whose answers would carry no mark,
unless the provider marks its text itself (evidence) or an administrator allowed it to answer unmarked.

Revision ID: t3xtm4rk
Revises: p11dur4t10n
Create Date: 2026-10-21 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "t3xtm4rk"
down_revision = "p11dur4t10n"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_providers", sa.Column("text_marking", postgresql.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_providers", "text_marking")
