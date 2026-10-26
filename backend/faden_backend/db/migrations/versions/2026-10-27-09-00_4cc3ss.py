"""The count of changes to who may do what, for the gateway to read at each request.

- ``access_epoch``: one row; triggers on the tables access is decided from move its count in the change's own
  transaction (``faden_backend/db/access_epoch.py``). The gateway uses only answers read since the count was
  what it is now (ASVS 5.0 V8.3.2, V7.4.1).

Revision ID: 4cc3ss
Revises: pwds3t
Create Date: 2026-10-27 09:00:00
"""

from alembic import op

from faden_backend.db import access_epoch

revision = "4cc3ss"
down_revision = "pwds3t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in access_epoch.create_all():
        op.execute(statement)


def downgrade() -> None:
    for statement in access_epoch.drop_triggers(access_epoch.WATCHED):
        op.execute(statement)
    op.execute(f"DROP FUNCTION IF EXISTS {access_epoch.FUNCTION}() CASCADE")
    op.execute("DROP TABLE IF EXISTS public.access_epoch")
