"""Teams in a tree: a parent, a cost centre, and the end of a team.

- ``groups.parent_id``: the team above (one parent; at most six teams from the top down, no cycles: checked by
  ``services/teams.py`` on every change). Deleting a team moves its sub-teams to its parent first; the
  foreign key's ``SET NULL`` only catches a delete made some other way.
- ``groups.cost_center``: a finance code, free text.
- ``groups.archived_at``: when the team ended. It keeps its history and takes no new members or home teams.

The gateway reads ``parent_id`` for the path it records with each request (its ``usage_group.py``); the
``groups`` table's trigger on the count of changes to access tells it when the tree changed.

Revision ID: u6t3amtr33
Revises: s3tep0ch
Create Date: 2026-10-28 14:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "u6t3amtr33"
down_revision = "s3tep0ch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "groups",
        sa.Column(
            "parent_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="SET NULL", name="fk_groups_parent_id"), nullable=True,
        ),
    )
    op.create_index("ix_groups_parent_id", "groups", ["parent_id"])
    op.add_column("groups", sa.Column("cost_center", sa.String(length=64), nullable=True))
    op.add_column("groups", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("groups", "archived_at")
    op.drop_column("groups", "cost_center")
    op.drop_index("ix_groups_parent_id", table_name="groups")
    op.drop_constraint("fk_groups_parent_id", "groups", type_="foreignkey")
    op.drop_column("groups", "parent_id")
