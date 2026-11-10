"""A restore makes each database exactly the backup: restored beside the live one, then swapped in.

``pg_restore --clean --if-exists`` into the live database dropped only what the dump holds. Tables a newer
release had made stayed, and a table one of them referred to could not be dropped, so it was not reloaded
either; the restore said so in a warning cut to 200 characters and then "Restore complete" (0.7.0rc1 on a test
server, 10 November 2026: 0.7.0's six tables stayed, and ``user`` and ``agents`` kept rows made after the backup).

Now each database of the backup is restored into a new, empty one beside it (``<db>__restore``, from
``template0``, owned like the live one) while the install keeps running. Any error of ``pg_restore`` fails the
restore there: the copies are dropped, the live databases were never touched. Restored into an empty database,
the dumps of an install restore without a single error or warning (every database of 0.6.0's upgrade backup,
Postgres 17.11), so there is no error class to let through. ``pg_restore``'s whole output goes to a log file.

With the services stopped, one transaction renames every live database to ``<db>__old`` and every copy to its
name: all of them or none. The caller starts the services; once they are healthy, :func:`drop_old` drops the
old ones. If they are not, :func:`swap_back` puts the old ones back, in one transaction too, and drops the copies.

The names come from the backup's manifest, so they are checked against :data:`_DB_NAME` before any of them reaches
SQL, and quoted as identifiers there (ASVS 5.0 1.2.4, 1.3.3 -- an identifier cannot be a bound parameter).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from faden_cli.core.registry import POSTGRES_CONTAINER

RESTORE_SUFFIX = "__restore"
OLD_SUFFIX = "__old"

#: What a database in a manifest may be called: what an install's databases are called (``faden_backend``,
#: ``rag``, ``pii``), short enough that the suffixes keep it within Postgres' 63 characters.
_DB_NAME = re.compile(r"\A[a-z_][a-z0-9_]{0,47}\Z")
#: Never a database of the backup: the server's own.
_NOT_RESTORED = frozenset({"postgres", "template0", "template1"})

#: Room kept free beyond the copies: the larger of this and a tenth of what they take. Postgres writes its WAL
#: while the copies load (up to max_wal_size, 1 GB by default, before a checkpoint recycles it).
RESERVE_BYTES = 1 << 30
#: A dump is compressed and holds no index: a database restored from it takes many times its size (10 to 25
#: times for the dumps of a small install, above an empty database's 7.5 MB). Used where no live database says
#: how big it is.
DUMP_FACTOR = 10

#: How often a rename is tried again when a connection came back between ending it and the rename.
_RENAME_TRIES = 3


class RestoreRefused(RuntimeError):
    """The restore cannot start (a name, a leftover of an earlier restore, the disk). Nothing was changed."""


class RestoreFailed(RuntimeError):
    """``pg_restore`` failed for a copy. The copies were dropped; the live databases were never touched."""


class SwapFailed(RuntimeError):
    """The swap's transaction failed: every rename rolled back, the live databases are the ones before."""


class PostgresError(RuntimeError):
    """A statement failed; the message carries Postgres' own."""


def container() -> str:
    """The Postgres container of the install (a module attribute, so a test points it at its own)."""
    return POSTGRES_CONTAINER


def quote(name: str) -> str:
    """*name* as a quoted SQL identifier; only for names :func:`check_names` let through."""
    return '"' + name.replace('"', '""') + '"'


def literal(text: str) -> str:
    """*text* as a quoted SQL string literal (``standard_conforming_strings`` is on since Postgres 9.1)."""
    return "'" + text.replace("'", "''") + "'"


def psql(sql: str, *, db: str = "postgres", docker: str = "docker") -> str:
    """Run *sql* as the superuser over the container's local socket; its rows (``-tA``) or :class:`PostgresError`.

    ``ON_ERROR_STOP``: the first failing statement ends it, with exit code 3.
    """
    proc = subprocess.run(  # noqa: S603
        [docker, "exec", "-i", container(), "psql", "-U", "postgres", "-X", "-q", "-t", "-A",
         "-v", "ON_ERROR_STOP=1", "-d", db],
        input=sql.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PostgresError(proc.stderr.decode("utf-8", errors="replace").strip() or f"psql exited {proc.returncode}")
    return proc.stdout.decode("utf-8", errors="replace")


def check_names(names: Iterable[str]) -> None:
    """Refuse a manifest whose databases are not named as an install's are (see :data:`_DB_NAME`)."""
    bad = sorted(n for n in names if not _DB_NAME.match(n) or n in _NOT_RESTORED)
    if bad:
        raise RestoreRefused(
            f"The backup's manifest names databases this restore does not take: {', '.join(map(repr, bad))}. "
            "Nothing was changed."
        )


def databases(docker: str = "docker") -> dict[str, int]:
    """Every database of the server (templates too) and its size in bytes."""
    rows = psql("SELECT datname || '|' || pg_database_size(datname) FROM pg_database ORDER BY 1;", docker=docker)
    found: dict[str, int] = {}
    for line in rows.splitlines():
        name, _, size = line.strip().rpartition("|")
        if name:
            found[name] = int(size)
    return found


def drop(names: Iterable[str], docker: str = "docker") -> list[str]:
    """``DROP DATABASE IF EXISTS`` each of *names*, one by one; the ones that could not be dropped, with why."""
    failed: list[str] = []
    for name in names:
        try:
            psql(f"DROP DATABASE IF EXISTS {quote(name)};", docker=docker)
        except PostgresError as exc:
            failed.append(f"{name} ({exc})")
    return failed


def leftovers(names: Iterable[str], existing: Iterable[str]) -> tuple[list[str], list[str]]:
    """The copies (``__restore``) and old databases (``__old``) an earlier restore of *names* left behind."""
    present = set(existing)
    copies = sorted(n + RESTORE_SUFFIX for n in names if n + RESTORE_SUFFIX in present)
    olds = sorted(n + OLD_SUFFIX for n in names if n + OLD_SUFFIX in present)
    return copies, olds


# ── The disk ──────────────────────────────────────────────────────


@dataclass
class DiskNeed:
    """What a restore needs on the disk, and what it found. Sizes in bytes."""

    databases: int = 0
    volumes: int = 0
    reserve: int = 0
    data_free: int = 0
    data_filesystem: str = ""
    host_free: int | None = None
    host_filesystem: str = ""
    detail: dict[str, int] = field(default_factory=dict)

    @property
    def shared(self) -> bool:
        """Whether the databases and the copies of the volumes land on the same filesystem."""
        return bool(self.data_filesystem) and self.data_filesystem == self.host_filesystem

    def problems(self) -> list[str]:
        """Why the restore cannot start for lack of room (empty when it can)."""
        found: list[str] = []
        if self.shared:
            need = self.databases + self.volumes + self.reserve
            if need > self.data_free:
                found.append(
                    f"{human(need)} free needed on {self.data_filesystem} (a copy of the databases {human(self.databases)}, "
                    f"of the volumes {human(self.volumes)}, {human(self.reserve)} kept free); {human(self.data_free)} free"
                )
            return found
        if self.databases + self.reserve > self.data_free:
            found.append(
                f"{human(self.databases + self.reserve)} free needed where Postgres keeps its data "
                f"({self.data_filesystem or 'its volume'}: a copy of the databases {human(self.databases)}, "
                f"{human(self.reserve)} kept free); {human(self.data_free)} free"
            )
        if self.volumes and self.host_free is not None and self.volumes > self.host_free:
            found.append(
                f"{human(self.volumes)} free needed for a copy of the volumes the backup replaces "
                f"({self.host_filesystem or 'the install folder'}); {human(self.host_free)} free"
            )
        return found


def human(size: int) -> str:
    """*size* in bytes as the operator reads it: ``1.2 GB``."""
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{size} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{size} bytes"  # unreachable


def data_free(docker: str = "docker") -> tuple[str, int]:
    """The filesystem Postgres keeps its data on, and its free bytes (``df`` in the container)."""
    directory = psql("SHOW data_directory;", docker=docker).strip()
    proc = subprocess.run(  # noqa: S603
        [docker, "exec", container(), "df", "-Pk", directory], capture_output=True, check=False
    )
    lines = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
    if proc.returncode != 0 or len(lines) < 2 or len(lines[-1].split()) < 4:
        raise RestoreRefused(
            f"How much room Postgres has could not be read (df in {container()}: "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()[:200] or 'no answer'}). Nothing was changed."
        )
    parts = lines[-1].split()
    return parts[0], int(parts[3]) * 1024


def estimate(dump_size: int, live_size: int | None, empty_size: int) -> int:
    """Room for a copy of one database: its live size, or an empty database plus :data:`DUMP_FACTOR` times its
    dump where that is more (a backup bigger than what runs now, a database the install does not have)."""
    return max(live_size or 0, empty_size + DUMP_FACTOR * dump_size)


def database_need(dumps: dict[str, int], live: dict[str, int]) -> tuple[int, dict[str, int]]:
    """Bytes the copies of the databases in *dumps* (name → dump size) take, in all and each."""
    empty = live.get("template0", 0)
    detail = {db: estimate(size, live.get(db), empty) for db, size in dumps.items()}
    return sum(detail.values()), detail


# ── Restore beside, swap, swap back ───────────────────────────────


def restore_beside(
    dumps: dict[str, Path],
    live: dict[str, int],
    log: BinaryIO,
    docker: str = "docker",
    *,
    started: Callable[[str, str], None] | None = None,
) -> list[str]:
    """Restore each dump (database name → dump file) into a new ``<db>__restore``; the copies made.

    Each copy is made from ``template0`` (``pg_restore``'s own advice for an empty target) with the live
    database's owner, encoding and locale where there is one, its database-wide settings and privileges carried
    over too (a dump without ``--create`` holds none of them). ``pg_restore`` runs with ``--verbose`` into *log*.
    Any exit code but 0 is a failure: every copy made so far is dropped and :class:`RestoreFailed` says which
    database failed and where its output is. The live databases are not touched.

    *started* (database, copy) is called as each database's restore begins, so what was printed before a
    failure names only the databases that were started.
    """
    made: list[str] = []
    for db, path in dumps.items():
        copy = db + RESTORE_SUFFIX
        if started is not None:
            started(db, copy)
        log.write(f"\n===== {db}: pg_restore into {copy} =====\n".encode())
        log.flush()
        try:
            psql(_create_statement(db, copy, live, docker), docker=docker)
            made.append(copy)
            _carry_over(db, copy, live, docker)
        except PostgresError as exc:
            _give_up(made, docker)
            raise RestoreFailed(f"{copy} could not be made: {exc}") from exc
        with path.open("rb") as dump:
            proc = subprocess.run(  # noqa: S603
                [docker, "exec", "-i", container(), "pg_restore", "-U", "postgres", "--verbose",
                 "--no-password", "-d", copy],
                stdin=dump,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        log.write(f"===== {db}: pg_restore exit code {proc.returncode} =====\n".encode())
        log.flush()
        if proc.returncode != 0:
            left = _give_up(made, docker)
            raise RestoreFailed(
                f"pg_restore failed for {db} (exit code {proc.returncode})"
                + (f"; {len(left)} copy(ies) could not be dropped: {', '.join(left)}" if left else "")
            )
    return made


def _create_statement(db: str, copy: str, live: dict[str, int], docker: str) -> str:
    if db not in live:
        return f"CREATE DATABASE {quote(copy)} TEMPLATE template0;"
    row = psql(
        "SELECT pg_get_userbyid(datdba) || '|' || pg_encoding_to_char(encoding) || '|' || datlocprovider::text || '|' "  # noqa: S608 -- names allow-listed by check_names, quoted by literal()/quote()
        f"|| datcollate || '|' || datctype FROM pg_database WHERE datname = {literal(db)};",
        docker=docker,
    ).strip()
    owner, encoding, provider, collate, ctype = row.split("|")
    if provider != "c":
        # An ICU or builtin locale: the copy takes the server's default, as CREATE DATABASE does; an install's
        # databases are all libc (made by initdb's defaults and shared/initdb).
        return f"CREATE DATABASE {quote(copy)} TEMPLATE template0 OWNER {quote(owner)} ENCODING {literal(encoding)};"
    return (
        f"CREATE DATABASE {quote(copy)} TEMPLATE template0 OWNER {quote(owner)} ENCODING {literal(encoding)} "
        f"LC_COLLATE {literal(collate)} LC_CTYPE {literal(ctype)};"
    )


def _carry_over(db: str, copy: str, live: dict[str, int], docker: str) -> None:
    """The live database's privileges and settings onto the copy: they belong to the database, not the dump."""
    if db not in live:
        return
    acl = psql(
        "SELECT coalesce(string_agg(CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) "  # noqa: S608 -- names allow-listed by check_names, quoted by literal()/quote()
        "END || '|' || a.privilege_type || '|' || a.is_grantable, E'\\n'), '') "
        f"FROM pg_database d, aclexplode(d.datacl) a WHERE d.datname = {literal(db)} AND d.datacl IS NOT NULL;",
        docker=docker,
    ).strip()
    statements: list[str] = []
    if acl:
        # A database with privileges of its own (datacl not null): the copy gets exactly those.
        statements.append(f"REVOKE ALL ON DATABASE {quote(copy)} FROM PUBLIC;")
        for line in acl.splitlines():
            grantee, privilege, grantable = line.split("|")
            statements.append(
                f"GRANT {privilege} ON DATABASE {quote(copy)} TO {grantee}"
                + (" WITH GRANT OPTION;" if grantable == "true" else ";")
            )
    settings = psql(
        "SELECT coalesce(string_agg(s, E'\\n'), '') FROM pg_db_role_setting r, unnest(r.setconfig) s, pg_database d "  # noqa: S608 -- names allow-listed by check_names, quoted by literal()/quote()
        f"WHERE r.setdatabase = d.oid AND r.setrole = 0 AND d.datname = {literal(db)};",
        docker=docker,
    ).strip()
    for line in settings.splitlines():
        name, _, value = line.partition("=")
        if re.fullmatch(r"[a-z_][a-z0-9_.]*", name):
            statements.append(f"ALTER DATABASE {quote(copy)} SET {name} = {literal(value)};")
    if statements:
        psql("\n".join(statements), docker=docker)


def _give_up(made: list[str], docker: str) -> list[str]:
    return drop(made, docker=docker)


@dataclass
class Swapped:
    """One database swapped in: whether a live one became ``<db>__old``."""

    name: str
    had_live: bool


def swap_in(names: list[str], live: Iterable[str], docker: str = "docker") -> list[Swapped]:
    """In one transaction: each live database of *names* → ``<db>__old``, each copy → its name.

    The connections to the live databases are ended first (the services are stopped by then; whatever still holds
    one would make the rename wait and fail). A rename that meets a connection that came back is tried again.
    :class:`SwapFailed` when it cannot be done: the transaction rolled back, nothing was renamed.
    """
    present = set(live)
    swapped = [Swapped(name, name in present) for name in names]
    statements = ["BEGIN;"]
    for item in swapped:
        if item.had_live:
            statements.append(f"ALTER DATABASE {quote(item.name)} RENAME TO {quote(item.name + OLD_SUFFIX)};")
        statements.append(f"ALTER DATABASE {quote(item.name + RESTORE_SUFFIX)} RENAME TO {quote(item.name)};")
    statements.append("COMMIT;")
    _renames([s.name for s in swapped if s.had_live], "\n".join(statements), docker)
    return swapped


def swap_back(swapped: list[Swapped], docker: str = "docker") -> list[str]:
    """Undo :func:`swap_in`: in one transaction each restored database → ``<db>__restore``, each ``<db>__old``
    → its name; then the restored copies are dropped. Returns the copies that could not be dropped.

    :class:`SwapFailed` when the renames cannot be done (nothing renamed: the restored databases stay live).
    """
    statements = ["BEGIN;"]
    for item in swapped:
        statements.append(f"ALTER DATABASE {quote(item.name)} RENAME TO {quote(item.name + RESTORE_SUFFIX)};")
        if item.had_live:
            statements.append(f"ALTER DATABASE {quote(item.name + OLD_SUFFIX)} RENAME TO {quote(item.name)};")
    statements.append("COMMIT;")
    _renames([s.name for s in swapped], "\n".join(statements), docker)
    return drop([s.name + RESTORE_SUFFIX for s in swapped], docker=docker)


def drop_old(swapped: list[Swapped], docker: str = "docker") -> list[str]:
    """Drop every ``<db>__old`` of a swap; the ones that could not be dropped, with why."""
    return drop([s.name + OLD_SUFFIX for s in swapped if s.had_live], docker=docker)


def _renames(busy: list[str], transaction: str, docker: str) -> None:
    end = ""
    if busy:
        names = ", ".join(literal(n) for n in busy)
        end = (
            f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname IN ({names}) "  # noqa: S608 -- names allow-listed by check_names, quoted by literal()/quote()
            "AND pid <> pg_backend_pid();\n"
        )
    last = ""
    for attempt in range(_RENAME_TRIES):
        try:
            psql(end + transaction, docker=docker)
            return
        except PostgresError as exc:
            last = str(exc)
            if "being accessed by other users" not in last:
                break
            time.sleep(1 + attempt)
    raise SwapFailed(last)
