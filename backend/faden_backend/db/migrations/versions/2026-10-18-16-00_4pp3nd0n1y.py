"""The audit trail is append-only in the database: no UPDATE, DELETE or TRUNCATE on audit_events.

The one change allowed is the database's own: ``actor_id`` set to NULL when that account is deleted (the
foreign key's ON DELETE SET NULL; the hash does not cover it, ``actor_email`` says who acted). Anyone who can
drop the trigger can still change the table; the hash chain, the file copy and the log copy show it
(services/audit/chain.py, ``faden audit verify``).

Revision ID: 4pp3nd0n1y
Revises: 4ud1tch41n
Create Date: 2026-10-18 16:00:00
"""

from alembic import op

revision = "4pp3nd0n1y"
down_revision = "4ud1tch41n"
branch_labels = None
depends_on = None


#: Refuses every change to an audit event but the account-deleted one.
FUNCTION = """
CREATE OR REPLACE FUNCTION faden_audit_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.actor_id IS NULL AND OLD.actor_id IS NOT NULL
       AND (to_jsonb(NEW) - 'actor_id') = (to_jsonb(OLD) - 'actor_id') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_events is append-only: % refused', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql
"""
TRIGGERS = (
    "CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION faden_audit_append_only()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION faden_audit_append_only()",
)


def upgrade() -> None:
    """Install the function and its two triggers."""
    op.execute(FUNCTION)
    for trigger in TRIGGERS:
        op.execute(trigger)


def downgrade() -> None:
    """Drop the triggers and their function."""
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS faden_audit_append_only()")
