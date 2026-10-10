"""The chat is for the roles that include it, and nobody who chats today loses it.

The gateway now checks the ``chat.*`` permissions, which it never did: any active account could chat,
whatever its roles. ``default_user`` has them. ``admin``, which gives every permission, gets them here
as well as in the seed. Every other account whose roles (its own or its groups') do not let it start a
chat gets ``default_user``, so it chats after the upgrade as it did before; an administrator takes the
role from accounts that should not chat. Superusers need no role.

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

#: An account whose roles, its own or its groups', cannot start a chat gets ``default_user``.
KEEP_CHATTING = """
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id
FROM "user" u, roles r
WHERE r.name = 'default_user'
  AND NOT u.is_superuser
  AND NOT EXISTS (
      SELECT 1
      FROM role_permissions rp
      JOIN permissions p ON p.id = rp.permission_id
      WHERE p.resource = 'chat.sessions' AND p.action = 'create'
        AND rp.role_id IN (SELECT ur.role_id FROM user_roles ur WHERE ur.user_id = u.id
                           UNION
                           SELECT gr.role_id FROM group_roles gr
                           JOIN user_groups ug ON ug.group_id = gr.group_id
                           WHERE ug.user_id = u.id)
  )
ON CONFLICT DO NOTHING
"""


def upgrade() -> None:
    """Give ``admin`` the chat, then ``default_user`` to every account that could not chat by its roles."""
    op.execute(ADMIN_CHATS)
    op.execute(KEEP_CHATTING)


def downgrade() -> None:
    """Nothing to undo that can be told apart: the roles given stay given."""
