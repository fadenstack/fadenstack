"""Langfuse's tables in a database of their own, so that every backup holds them.

``write_env_file(..., preserve_secrets=True)`` (``tune_env`` calls it on every deploy and upgrade) rebuilt
Langfuse's database URL from ``POSTGRES_DB``, which is ``postgres``. So every install's Langfuse ran on the
server's own database, which a backup leaves out (``backup.existing_databases``) and a restore never
replaces (``restore_swap._NOT_RESTORED``): a restore lost Langfuse's users, projects, API keys and prompts
(its traces are in ClickHouse). The database ``langfuse`` that ``shared/initdb`` makes for it stayed empty.

The URL now keeps the database it names (``env_gen.write_env_file``), and :func:`move_step` moves an
install's Langfuse into ``langfuse`` once, during ``faden upgrade``'s restart, with Postgres running and
before the services start on the new ``.env``:

1. Langfuse is stopped, so nothing writes while it is copied.
2. ``postgres`` is dumped (``pg_dump -Fc``) and restored into a fresh ``langfuse``, made from ``template0``
   like ``postgres`` (owner, encoding, locale), with ``--exit-on-error``. The database ``langfuse`` is
   replaced only when it holds no table: what initdb made, or what a failed move emptied again.
3. Every table of ``postgres`` is counted on both sides; a difference fails the move.
4. Only then is the URL switched in ``.env``; Langfuse starts on it with the rest of the services.

Anything that fails leaves the URL as it was and ``langfuse`` empty again: Langfuse runs on ``postgres`` as
before, and every backup says that it leaves Langfuse out. The tables also stay in ``postgres``: going back
to an older release with a backup made before puts back that release's ``.env``, whose URL names
``postgres``.

Database names reach SQL only as the constants below, quoted as identifiers (ASVS 5.0 1.2.4).
"""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from faden_cli.core import restore_swap
from faden_cli.core.env_gen import read_env_file, set_env_values

#: Where Langfuse's tables belong, and where every install's Langfuse ran until this release.
OWN = "langfuse"
SERVER = "postgres"
#: The services that write to Langfuse's database; stopped while it is copied.
SERVICES = ("langfuse-web", "langfuse-worker")
#: Every install's Langfuse has this table (Prisma's record of its migrations).
_MARKER = "_prisma_migrations"

USER_RELATIONS = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') "
    "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%';"
)
_TABLES = (
    "SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
    "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1;"
)


class MoveFailed(RuntimeError):  # noqa: N818 -- named like restore_swap's RestoreFailed
    """The copy could not be made or does not match; ``langfuse`` was emptied again, the URL unchanged."""


def database_of(url: str) -> str:
    """The database a Postgres URL names; :data:`OWN` when it names none."""
    name = urlsplit(url).path.lstrip("/")
    return name or OWN


def with_database(url: str, database: str) -> str:
    """*url* naming *database* instead; the rest of it (the password too) as it was."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def runs_on_server_database(env: dict[str, str]) -> bool:
    """Whether the install's Langfuse runs on ``postgres``, which a backup leaves out."""
    url = env.get("LANGFUSE_DATABASE_URL", "")
    return bool(url) and database_of(url) == SERVER


def _count(sql: str, db: str, docker: str) -> int:
    return int(restore_swap.psql(sql, db=db, docker=docker).strip() or 0)


def _exists(db: str, docker: str) -> bool:
    return db in restore_swap.databases(docker)


def _has_langfuse(db: str, docker: str) -> bool:
    marker = restore_swap.literal(_MARKER)
    sql = f"SELECT count(*) FROM pg_class WHERE relname = {marker} AND relkind = 'r';"  # noqa: S608 -- a constant
    return _count(sql, db, docker) > 0


def table_counts(db: str, docker: str = "docker") -> dict[str, int]:
    """Every table of *db* outside the system schemas, and its exact row count."""
    rows = restore_swap.psql(_TABLES, db=db, docker=docker)
    names = [line.strip() for line in rows.splitlines() if line.strip()]
    if not names:
        return {}
    # The names come from the catalogue, quote_ident'ed there; the label is a quoted literal.
    union = " UNION ALL ".join(
        f"SELECT {restore_swap.literal(name)} || '|' || count(*) FROM {name}" for name in names  # noqa: S608
    )
    counts: dict[str, int] = {}
    for line in restore_swap.psql(union + ";", db=db, docker=docker).splitlines():
        name, _, count = line.strip().rpartition("|")
        if name:
            counts[name] = int(count)
    return counts


def _empty_again(docker: str) -> None:
    """``langfuse`` as initdb made it: dropped and made again, empty."""
    restore_swap.psql(f"DROP DATABASE IF EXISTS {restore_swap.quote(OWN)};", docker=docker)
    restore_swap.psql(f"CREATE DATABASE {restore_swap.quote(OWN)};", docker=docker)


def _dump_server_database(dump: Path, docker: str) -> None:
    with dump.open("wb") as out:
        proc = subprocess.run(  # noqa: S603
            [docker, "exec", restore_swap.container(), "pg_dump", "-U", "postgres", "-Fc", SERVER],
            stdout=out, stderr=subprocess.PIPE, check=False,
        )
    if proc.returncode != 0:
        why = proc.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise MoveFailed(f"pg_dump of {SERVER}: {why}")


def _restore_into_own(dump: Path, docker: str) -> None:
    live = restore_swap.databases(docker)
    restore_swap.psql(f"DROP DATABASE IF EXISTS {restore_swap.quote(OWN)};", docker=docker)
    # Made like `postgres`: its owner, encoding and locale (restore_swap's statement for a copy).
    restore_swap.psql(restore_swap._create_statement(SERVER, OWN, live, docker), docker=docker)  # noqa: SLF001
    with dump.open("rb") as source:
        restored = subprocess.run(  # noqa: S603
            [docker, "exec", "-i", restore_swap.container(), "pg_restore", "-U", "postgres",
             "--no-password", "--exit-on-error", "-d", OWN],
            stdin=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
    if restored.returncode != 0:
        why = restored.stdout.decode("utf-8", errors="replace").strip()[-300:]
        raise MoveFailed(f"pg_restore into {OWN}: {why}")


def copy_into_own(docker: str = "docker") -> dict[str, int]:
    """Copy ``postgres`` into a fresh ``langfuse`` and compare every table's rows; the counts compared.

    The caller has stopped Langfuse and checked that ``langfuse`` holds no table. :class:`MoveFailed` when
    the copy cannot be made or differs: ``langfuse`` is then empty again.
    """
    with tempfile.TemporaryDirectory(prefix="faden-langfuse-") as work:
        dump = Path(work) / "langfuse.dump"
        _dump_server_database(dump, docker)
        try:
            _restore_into_own(dump, docker)
            before, after = table_counts(SERVER, docker), table_counts(OWN, docker)
            if not before or before != after:
                differ = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
                which = ", ".join(differ[:5]) or "every table"
                raise MoveFailed(f"the copy differs from {SERVER} in {which}")
        except (MoveFailed, restore_swap.PostgresError) as exc:
            with contextlib.suppress(restore_swap.PostgresError):
                _empty_again(docker)
            raise MoveFailed(str(exc)) from exc
        return after


@dataclass
class Moved:
    """What :func:`move` did: ``moved`` with the tables copied, or why it left Langfuse where it was."""

    moved: bool = False
    tables: int = 0
    rows: int = 0
    reason: str = ""


def move(env_path: Path, stop: Callable[[list[str]], int], docker: str = "docker") -> Moved:
    """Move the install's Langfuse from ``postgres`` into ``langfuse``; Postgres must be running.

    *stop* is called with :data:`SERVICES` before the copy (the caller's ``docker compose stop``) and returns
    its exit code; the copy is made only once they stopped. Nothing is done when the URL names another
    database. A Langfuse that never made a table there gets the URL only.
    """
    env = read_env_file(env_path)
    if not runs_on_server_database(env):
        return Moved()
    url = env["LANGFUSE_DATABASE_URL"]
    try:
        if _exists(OWN, docker) and _count(USER_RELATIONS, OWN, docker):
            return Moved(reason=f"the database {OWN} is not empty, so it was not replaced")
        if not _has_langfuse(SERVER, docker):
            if not _exists(OWN, docker):
                restore_swap.psql(f"CREATE DATABASE {restore_swap.quote(OWN)};", docker=docker)
            set_env_values(env_path, {"LANGFUSE_DATABASE_URL": with_database(url, OWN)})
            return Moved(moved=True)
        if stop(list(SERVICES)) != 0:
            return Moved(reason="Langfuse could not be stopped for the copy")
        counts = copy_into_own(docker)
    except (MoveFailed, restore_swap.PostgresError) as exc:
        return Moved(reason=str(exc))
    set_env_values(env_path, {"LANGFUSE_DATABASE_URL": with_database(url, OWN)})
    return Moved(moved=True, tables=len(counts), rows=sum(counts.values()))


def move_step(ctx: object, env_path: Path) -> None:
    """``faden upgrade``'s step: :func:`move`, said on the console."""
    from faden_cli.core.compose import stop as compose_stop  # noqa: PLC0415
    from faden_cli.core.console import info, success, warning  # noqa: PLC0415

    if not env_path.exists() or not runs_on_server_database(read_env_file(env_path)):
        return
    info("Moving Langfuse's tables into their own database (backups leave out the server's own)…")
    done = move(env_path, lambda services: compose_stop(ctx, services=services))  # type: ignore[arg-type]
    if done.moved:
        success(
            f"Langfuse now uses the database {OWN} ({done.tables} tables, {done.rows} rows copied), so "
            f"backups hold it. Its old tables stay in {SERVER} for going back to an older release."
        )
    else:
        warning(
            f"Langfuse stays on the database {SERVER}, which a backup leaves out ({done.reason}). Its "
            "users, projects, API keys and prompts are not in your backups. Run `faden upgrade` again, or "
            "see Help: Backups."
        )
