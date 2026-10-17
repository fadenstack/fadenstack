"""The audit trail's order, who and from where, its hash chain; the auditor role; viewers lose the trail.

- ``audit_events`` gains ``seq`` (order of writing; existing rows numbered as stored), ``actor_email``,
  ``client_ip``, ``request_id``, ``user_agent``, ``source`` and ``prev_hash``/``hash``. Existing rows keep
  NULL hashes: the backend's first start writes ``audit.chain.start``, which covers them with a digest.
- ``actor_email`` is filled for existing rows whose user still exists, so the trail keeps naming them when
  an account is deleted later.
- The built-in ``viewer`` and ``operator`` roles lose ``audit:read``: the trail now holds e-mail and IP
  addresses. Administrators and the new built-in ``auditor`` role (created at start-up with the other
  built-in roles) read it. Roles an organisation made itself are not touched.

Revision ID: 4ud1tch41n
Revises: s1gn1nr3v
Create Date: 2026-10-18 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "4ud1tch41n"
down_revision = "s1gn1nr3v"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the columns, fill the e-mail of existing rows, take the trail from viewers and operators."""
    op.add_column(
        "audit_events", sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )
    op.create_unique_constraint("uq_audit_events_seq", "audit_events", ["seq"])
    op.add_column("audit_events", sa.Column("actor_email", sa.String(length=320), nullable=True))
    op.add_column("audit_events", sa.Column("client_ip", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("user_agent", sa.String(length=256), nullable=True))
    op.add_column(
        "audit_events",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="console"),
    )
    op.add_column("audit_events", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("hash", sa.String(length=64), nullable=True))
    op.create_index("ix_audit_events_actor_email", "audit_events", ["actor_email"])

    op.execute(
        'UPDATE audit_events AS a SET actor_email = u.email FROM "user" AS u '
        "WHERE a.actor_id = u.id AND a.actor_email IS NULL",
    )
    op.execute(
        """
        DELETE FROM role_permissions AS rp
        USING roles AS r, permissions AS p
        WHERE rp.role_id = r.id AND rp.permission_id = p.id
          AND r.name IN ('viewer', 'operator') AND r.is_builtin
          AND p.resource = 'audit' AND p.action = 'read'
        """,
    )


def downgrade() -> None:
    """Drop the columns again (the roles' permission is not given back)."""
    op.drop_index("ix_audit_events_actor_email", table_name="audit_events")
    for column in ("hash", "prev_hash", "source", "user_agent", "request_id", "client_ip", "actor_email"):
        op.drop_column("audit_events", column)
    op.drop_constraint("uq_audit_events_seq", "audit_events", type_="unique")
    op.drop_column("audit_events", "seq")
