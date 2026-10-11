"""The chat is for the roles that include it.

The gateway now checks the ``chat.*`` permissions, which it never did: any active account could chat,
whatever its roles. ``default_user`` has them. ``admin``, which gives every permission, gets them here
as well as in the seed. No account is given a role: one whose roles do not include the chat stops
chatting until an administrator adds ``default_user`` (Help, Users). Superusers need no role.

Revision ID: ch4tr0l3s
Revises: t0k3nt3n4nt
"""

from __future__ import annotations

from alembic import op

revision = "ch4tr0l3s"
down_revision = "t0k3nt3n4nt"
branch_labels = None
depends_on = None

#: The chat's permissions, as ``default_user`` has them.
CHAT_PERMISSIONS = (
    ("chat.projects", "create"),
    ("chat.projects", "read"),
    ("chat.projects", "update"),
    ("chat.projects", "delete"),
    ("chat.sessions", "create"),
    ("chat.sessions", "read"),
    ("chat.sessions", "update"),
    ("chat.sessions", "delete"),
    ("chat.attachments", "create"),
    ("chat.attachments", "read"),
    ("chat.attachments", "delete"),
)

_PAIRS = ", ".join(f"('{resource}', '{action}')" for resource, action in CHAT_PERMISSIONS)

#: ``admin`` gets the chat's permissions (those that exist: the seed makes any missing).
ADMIN_CHATS = f"""
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'admin' AND (p.resource, p.action) IN ({_PAIRS})
ON CONFLICT DO NOTHING
"""  # noqa: S608 - constants above, no input


def upgrade() -> None:
    """Give ``admin`` the chat."""
    op.execute(ADMIN_CHATS)


def downgrade() -> None:
    """Nothing to undo that can be told apart: admin keeps the chat."""
