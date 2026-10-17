"""Seal what people wrote that was still plain: project names, descriptions, file names, metadata, tool errors.

Only types are widened here (``varchar`` to ``text``, which Postgres changes without rewriting the table;
``json`` to ``text``, rewritten), and ``llm_tool_call_log.error_kind`` is added and filled for existing
failures. No value is sealed in the migration: the migrator would hold every service while it ran.
`faden secrets seal` (run by `faden upgrade` after its health check) seals the existing values; the columns
read plain and sealed values side by side meanwhile.

Revision ID: s34lr3st
Revises: pr0vk3yg
Create Date: 2026-10-18 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "s34lr3st"
down_revision = "pr0vk3yg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Widen the types, add the kind of a tool's failure."""
    op.alter_column(
        "chat_project", "name", type_=sa.Text(), existing_type=sa.String(256), existing_nullable=False,
    )
    op.alter_column("chat_attachment", "filename", type_=sa.Text(), existing_type=sa.String(512))
    for table in ("chat_project", "chat_session"):
        op.alter_column(
            table, "metadata_json", type_=sa.Text(), existing_type=sa.JSON(),
            postgresql_using="metadata_json::text",
        )
    op.add_column("llm_tool_call_log", sa.Column("error_kind", sa.String(length=32), nullable=True))
    op.execute("UPDATE llm_tool_call_log SET error_kind = 'error' WHERE is_error AND error_kind IS NULL")


def downgrade() -> None:
    """Drop the kind; the types stay text (sealed values do not fit the old ones)."""
    op.drop_column("llm_tool_call_log", "error_kind")
