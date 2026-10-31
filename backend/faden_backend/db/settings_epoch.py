"""The count of changes to the system settings (``system_settings_epoch``), kept by the database itself.

Each backend process (every uvicorn worker, the taskiq worker) holds the settings an administrator can change
live in its own ``settings`` object. It reads this count once per request and once per task, and when the
count has moved since it last loaded them, loads them all again
(``services/system_settings/runtime_sync.py``). Triggers on ``system_setting_value`` and
``system_setting_secret`` move the count in the same transaction as the change, so every way of writing
(the console, a module switch, the server's CLI, a migration, SQL by hand) reaches every process, and the
new count is seen exactly when the change is. The same design as
``access_epoch`` (``db/access_epoch.py``), for a different set of tables, with one difference: each new
count is drawn from a sequence instead of adding one, so a number never comes back. A process may read the
count inside a transaction that changed a setting itself (``refresh_runtime_values`` reads through its
caller's session); were that transaction rolled back, the next change would otherwise count to the same
number again and look like the one already loaded. A process only compares the count for equality.

Used by the migration that made it and by the tests.
"""

from __future__ import annotations

#: The function the triggers call.
FUNCTION = "public.faden_settings_changed"

#: Where each new count comes from.
SEQUENCE = "public.system_settings_epoch_seq"

#: The tables whose every change moves the count.
WATCHED: tuple[str, ...] = ("system_setting_value", "system_setting_secret")

#: The trigger's name on each of them.
TRIGGER = "faden_settings"

#: The query a process reads the count with.
SQL = "SELECT epoch FROM system_settings_epoch WHERE id = 1"

COUNTER = [
    f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE}",
    "CREATE TABLE IF NOT EXISTS public.system_settings_epoch "
    "(id smallint PRIMARY KEY CHECK (id = 1), epoch bigint NOT NULL)",
    "INSERT INTO public.system_settings_epoch (id, epoch) VALUES (1, 0) ON CONFLICT (id) DO NOTHING",
    f"""CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.system_settings_epoch SET epoch = nextval('{SEQUENCE}') WHERE id = 1;
    RETURN NULL;
END
$$""",  # noqa: S608 - the names are this module's constants, not input
]


def triggers() -> list[str]:
    """The statements that make each watched table move the count (made anew)."""
    statements = []
    for table in WATCHED:
        statements += [
            f"DROP TRIGGER IF EXISTS {TRIGGER} ON public.{table}",
            f"CREATE TRIGGER {TRIGGER} AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()",
        ]
    return statements


def drop_all() -> list[str]:
    """The statements that take the triggers, the function and the count away again."""
    return [
        *(f"DROP TRIGGER IF EXISTS {TRIGGER} ON public.{table}" for table in WATCHED),
        f"DROP FUNCTION IF EXISTS {FUNCTION}()",
        "DROP TABLE IF EXISTS public.system_settings_epoch",
        f"DROP SEQUENCE IF EXISTS {SEQUENCE}",
    ]


def create_all() -> list[str]:
    """Everything the count needs: the sequence, the table and its row, the function and the triggers."""
    return [*COUNTER, *triggers()]
