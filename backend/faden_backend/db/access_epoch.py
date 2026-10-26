"""The count of changes to who may do what (``access_epoch``), kept by the database itself.

The gateway keeps answers about access in caches of its own (a person's roles and teams, agents and their
grants, API tokens, logouts, Enterprise's exclusions and limits) and reads this count once per request; an
answer read before the count last moved is not used (the gateway's ``services/gateway/access_epoch.py``;
ASVS 5.0 V8.3.2, V7.4.1). Triggers move the count in the same transaction as the change, so no way of writing
(the console, provisioning, the server's CLI, a migration, SQL by hand) leaves the gateway with an old answer,
and the new count is seen exactly when the change is.

Each change is one update of the table's single row, which is held until the change commits: two changes
wait for each other, and in the rare case they wait in a circle, PostgreSQL ends one of them, which is then
tried again. Used by the migration that made it, by Enterprise's for its own tables, and by the tests.
"""

from __future__ import annotations

#: The function the triggers call.
FUNCTION = "public.faden_access_changed"

#: Core's tables, and for those written for other reasons too, the columns whose change matters.
WATCHED: dict[str, tuple[str, ...]] = {
    "user": ("is_active", "is_superuser", "sessions_valid_after"),
    "user_roles": (),
    "group_roles": (),
    "user_groups": (),
    "roles": (),
    "role_permissions": (),
    "permissions": (),
    "groups": (),
    "api_tokens": ("user_id", "tenant_id", "revoked_at", "expires_at"),
    "revoked_sign_ins": (),
    "agents": (),
    "agent_versions": (),
    "agent_grants": (),
    "agent_members": (),
    "tenant_restrictions": (),
}

COUNTER = [
    "CREATE TABLE IF NOT EXISTS public.access_epoch "
    "(id smallint PRIMARY KEY CHECK (id = 1), epoch bigint NOT NULL)",
    "INSERT INTO public.access_epoch (id, epoch) VALUES (1, 0) ON CONFLICT (id) DO NOTHING",
    f"""CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.access_epoch SET epoch = epoch + 1 WHERE id = 1;
    RETURN NULL;
END
$$""",  # noqa: S608 - the name is this module's constant, not input
]


def _name(schema: str, table: str) -> str:
    return f'{schema}."{table}"'


def triggers(
    tables: dict[str, tuple[str, ...]], *, schema: str = "public", function: str = FUNCTION,
) -> list[str]:
    """The statements that make each of *tables* move the count through *function* (made anew)."""
    statements = []
    for table, columns in tables.items():
        target = _name(schema, table)
        statements += [
            f"DROP TRIGGER IF EXISTS faden_access ON {target}",
            f"DROP TRIGGER IF EXISTS faden_access_columns ON {target}",
        ]
        events = "INSERT OR DELETE OR TRUNCATE" if columns else "INSERT OR UPDATE OR DELETE OR TRUNCATE"
        statements.append(
            f"CREATE TRIGGER faden_access AFTER {events} ON {target} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {function}()",
        )
        if columns:
            statements.append(
                f"CREATE TRIGGER faden_access_columns AFTER UPDATE OF {', '.join(columns)} ON {target} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION {function}()",
            )
    return statements


def drop_triggers(tables: dict[str, tuple[str, ...]], *, schema: str = "public") -> list[str]:
    """The statements that take the triggers off *tables* again."""
    statements = []
    for table in tables:
        statements += [
            f"DROP TRIGGER IF EXISTS faden_access ON {_name(schema, table)}",
            f"DROP TRIGGER IF EXISTS faden_access_columns ON {_name(schema, table)}",
        ]
    return statements


def create_all() -> list[str]:
    """Everything core needs: the counter, its function and core's triggers."""
    return [*COUNTER, *triggers(WATCHED)]
