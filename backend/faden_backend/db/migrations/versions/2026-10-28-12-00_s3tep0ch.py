"""The count of changes to the system settings, for every backend process to read before it works.

- ``system_settings_epoch``: one row; triggers on ``system_setting_value`` and ``system_setting_secret`` move
  its count in the change's own transaction (``faden_backend/db/settings_epoch.py``). Each process loads the
  settings again when the count has moved since it last did (``services/system_settings/runtime_sync.py``).

Revision ID: s3tep0ch
Revises: tk3nus3
Create Date: 2026-10-28 12:00:00
"""

from alembic import op

from faden_backend.db import settings_epoch

revision = "s3tep0ch"
down_revision = "tk3nus3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in settings_epoch.create_all():
        op.execute(statement)


def downgrade() -> None:
    for statement in settings_epoch.drop_all():
        op.execute(statement)
