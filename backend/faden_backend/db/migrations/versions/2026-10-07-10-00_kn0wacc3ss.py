"""Knowledge collections get an access setting, and only knowledge managers change the knowledge base.

A collection is open to everyone (what every collection was until now) or only to the people and
groups it is granted to; a restricted collection hides everything below it too. ``default_user``,
the role every new account gets, could create, change and delete any collection and document
through the API: those rights go, here as well as in the seed, which only ever adds.

Revision ID: kn0wacc3ss
Revises: 4p1t0k3n
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "kn0wacc3ss"
down_revision = "4p1t0k3n"
branch_labels = None
depends_on = None

#: What ``default_user`` keeps of the knowledge base is reading it.
_TAKEN_FROM_DEFAULT_USER = (
    ("rag.containers", "create"),
    ("rag.containers", "update"),
    ("rag.containers", "delete"),
    ("rag.assets", "create"),
    ("rag.assets", "update"),
    ("rag.assets", "delete"),
    ("rag.publish", "execute"),
    ("rag.search", "write"),
)


def upgrade() -> None:
    """Add the access setting and the grants; take the write rights from ``default_user``."""
    op.add_column(
        "rag_lite_collections",
        sa.Column("access", sa.String(16), nullable=False, server_default="everyone"),
    )
    op.create_table(
        "rag_lite_collection_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id",
            UUID(as_uuid=True),
            sa.ForeignKey("rag_lite_collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_type", sa.String(8), nullable=False),
        sa.Column("principal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "collection_id", "principal_type", "principal_id", name="uq_rag_lite_collection_grant",
        ),
    )
    op.create_index(
        "ix_rag_lite_collection_grants_collection_id", "rag_lite_collection_grants", ["collection_id"],
    )
    pairs = ", ".join(f"('{resource}', '{action}')" for resource, action in _TAKEN_FROM_DEFAULT_USER)
    op.execute(
        f"""
        DELETE FROM role_permissions rp
        USING roles r, permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND r.name = 'default_user'
          AND (p.resource, p.action) IN ({pairs})
        """  # noqa: S608 - constants above, no input
    )


def downgrade() -> None:
    """Drop the grants and the access setting (the rights taken from ``default_user`` stay taken)."""
    op.drop_index("ix_rag_lite_collection_grants_collection_id", table_name="rag_lite_collection_grants")
    op.drop_table("rag_lite_collection_grants")
    op.drop_column("rag_lite_collections", "access")
