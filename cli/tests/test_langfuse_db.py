"""Langfuse's tables move into their own database, so that a backup holds them and a restore brings them back.

Every install's Langfuse ran on the server's own database ``postgres`` (tune_env rebuilt its URL from
``POSTGRES_DB``), which a backup leaves out: a restore lost Langfuse's users, projects, API keys and prompts
(tracker 2686, release plan §12.2 C1). The real-Postgres tests run Postgres in a container of their own (the
install's image) and talk to it through ``docker exec``, as the move and the restore do; nothing else is
reached.
"""

from __future__ import annotations

import inspect
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from faden_cli.commands import upgrade
from faden_cli.core import backup, env_gen, langfuse_db, restore_swap
from faden_cli.core.env_gen import read_env_file, write_env_file

IMAGE = "pgvector/pgvector:pg17"
REAL_RUN = subprocess.run  # before conftest's guard wraps it for each test

URL = "postgresql://postgres:s3cret@postgres:5432/{}"


# ── The URL ───────────────────────────────────────────────────────


def test_the_database_a_url_names() -> None:
    assert langfuse_db.database_of(URL.format("postgres")) == "postgres"
    assert langfuse_db.database_of(URL.format("langfuse")) == "langfuse"
    assert langfuse_db.database_of("postgresql://u:p@postgres:5432") == "langfuse", "none named: its own"
    assert langfuse_db.with_database(URL.format("postgres"), "langfuse") == URL.format("langfuse")


def _env(tmp_path: Path, url: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(
        f"POSTGRES_USER=postgres\nPOSTGRES_PASSWORD=s3cret\nPOSTGRES_DB=postgres\nLANGFUSE_DATABASE_URL={url}\n",
        encoding="utf-8",
    )
    return env


@pytest.mark.parametrize("database", ["langfuse", "postgres"])
def test_rewriting_env_keeps_the_database_langfuse_runs_on(tmp_path: Path, database: str) -> None:
    """tune_env rewrites .env with the secrets kept on every deploy and upgrade; the URL named POSTGRES_DB."""
    env = _env(tmp_path, URL.format(database))
    variables = read_env_file(env)
    variables["LANGFUSE_DATABASE_URL"] = URL.format("langfuse").replace("s3cret", "new-default")

    write_env_file(env, variables, preserve_secrets=True)

    assert read_env_file(env)["LANGFUSE_DATABASE_URL"] == URL.format(database)


def test_a_missing_password_keeps_the_database_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    url = URL.format("postgres")
    env.write_text(f"POSTGRES_USER=postgres\nLANGFUSE_DATABASE_URL={url}\n", encoding="utf-8")
    monkeypatch.setattr(env_gen, "REQUIRED_KEYS", ("POSTGRES_PASSWORD",))
    monkeypatch.setattr(env_gen, "_TOKEN_GROUPS", ())
    monkeypatch.setattr(env_gen, "DEPLOY_SETTINGS", {})

    env_gen.fill_required_env(env)

    after = read_env_file(env)
    assert after["LANGFUSE_DATABASE_URL"].endswith("@postgres:5432/postgres")
    assert after["POSTGRES_PASSWORD"] in after["LANGFUSE_DATABASE_URL"]


def test_the_upgrade_moves_langfuse_after_postgres_is_up_and_before_the_services_start() -> None:
    source = inspect.getsource(upgrade._restart_and_check)
    steps = ("_sync_postgres_password(ctx", "move_langfuse(ctx", "rc = compose_up(")
    sync, move, start = (source.index(s) for s in steps)
    assert sync < move < start


@pytest.mark.parametrize(("database", "warned"), [("postgres", True), ("langfuse", False)])
def test_a_backup_says_when_it_leaves_langfuse_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: str, warned: bool,
) -> None:
    said: list[str] = []
    monkeypatch.setattr(backup, "warning", said.append)
    ctx = SimpleNamespace(env_file=_env(tmp_path, URL.format(database)))

    backup._warn_if_langfuse_left_out(ctx)  # type: ignore[arg-type]

    assert bool(said) is warned
    assert not said or "not in this backup" in said[0]
    assert "_warn_if_langfuse_left_out(ctx)" in inspect.getsource(backup.create_backup)


# ── On a real Postgres ────────────────────────────────────────────


def _docker_ready() -> bool:
    docker = shutil.which("docker")
    return bool(docker) and REAL_RUN([docker, "info"], capture_output=True, check=False).returncode == 0


needs_docker = pytest.mark.skipif(not _docker_ready(), reason="needs Docker, for a real Postgres")


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    """A Postgres of the install's image, only for these tests, removed afterwards."""
    name = f"faden-cli-langfuse-test-{secrets.token_hex(4)}"
    started = REAL_RUN(
        ["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=test-only",
         "--tmpfs", "/var/lib/postgresql/data:rw,size=2g", IMAGE],
        capture_output=True, check=False,
    )
    assert started.returncode == 0, started.stderr.decode(errors="replace")
    try:
        for _ in range(60):
            if REAL_RUN(["docker", "exec", name, "psql", "-U", "postgres", "-tAc", "SELECT 1"],
                        capture_output=True, check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("the test Postgres did not start")
        yield name
    finally:
        REAL_RUN(["docker", "rm", "-f", name], capture_output=True, check=False)


def _sql(text: str, *, pg: str, db: str = "postgres") -> str:
    proc = REAL_RUN(
        ["docker", "exec", "-i", pg, "psql", "-U", "postgres", "-X", "-q", "-t", "-A",
         "-v", "ON_ERROR_STOP=1", "-d", db],
        input=text.encode(), capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    return proc.stdout.decode().strip()


@pytest.fixture
def db(pg: str, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The code pointed at the test Postgres, which holds what an install has.

    That is ``postgres`` with the vector extension (initdb makes it there) and an empty ``langfuse``. A
    ``docker exec`` anywhere else fails the test.
    """

    def only_ours(args: Any, *rest: Any, **kwargs: Any) -> Any:
        if isinstance(args, (list, tuple)) and args and "docker" in str(args[0]).lower() and "exec" in args:
            assert pg in args, f"a docker exec outside the test Postgres: {args!r}"
        return REAL_RUN(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", only_ours)
    monkeypatch.setattr(restore_swap, "POSTGRES_CONTAINER", pg)
    monkeypatch.setattr(backup, "POSTGRES_CONTAINER", pg)
    for name in _sql("SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'",
                     pg=pg).split():
        _sql(f'DROP DATABASE "{name}" WITH (FORCE)', pg=pg)
    _sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public;"
         "CREATE EXTENSION IF NOT EXISTS vector; CREATE DATABASE langfuse;", pg=pg)
    return SimpleNamespace(name=pg, sql=lambda text, d="postgres": _sql(text, pg=pg, db=d))


def _langfuse_in_postgres(db: SimpleNamespace) -> None:
    """What a Langfuse leaves in ``postgres``.

    Prisma's record, users, projects, API keys, prompts, with foreign keys, a sequence, an index and an enum.
    """
    db.sql(
        "CREATE TYPE \"Role\" AS ENUM ('OWNER', 'MEMBER');"
        "CREATE TABLE _prisma_migrations (id text PRIMARY KEY, migration_name text NOT NULL);"
        'CREATE TABLE users (id text PRIMARY KEY, email text UNIQUE, role "Role" NOT NULL);'
        "CREATE TABLE projects (id text PRIMARY KEY, name text NOT NULL);"
        "CREATE TABLE api_keys (id text PRIMARY KEY, project_id text REFERENCES projects(id), "
        "hashed_secret_key text NOT NULL);"
        "CREATE TABLE prompts (id serial PRIMARY KEY, project_id text REFERENCES projects(id), prompt text);"
        "CREATE INDEX prompts_project ON prompts (project_id);"
        "INSERT INTO _prisma_migrations VALUES ('1', 'init'), ('2', 'v3');"
        "INSERT INTO users VALUES ('u1', 'admin@example.com', 'OWNER');"
        "INSERT INTO projects VALUES ('p1', 'fadenstack');"
        "INSERT INTO api_keys VALUES ('k1', 'p1', 'hash');"
        "INSERT INTO prompts (project_id, prompt) VALUES ('p1', 'a'), ('p1', 'b');"
    )


def _rows(db: SimpleNamespace, name: str) -> dict[str, list[str]]:
    tables = db.sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1", name).split()
    return {t: db.sql(f'SELECT t::text FROM "{t}" t ORDER BY 1', name).splitlines() for t in tables}  # noqa: S608


def _stopped(calls: list[list[str]], code: int = 0) -> Any:
    def stop(services: list[str]) -> int:
        calls.append(services)
        return code

    return stop


@needs_docker
def test_the_move_copies_every_row_and_switches_the_url(db: SimpleNamespace, tmp_path: Path) -> None:
    _langfuse_in_postgres(db)
    before = _rows(db, "postgres")
    env = _env(tmp_path, URL.format("postgres"))
    calls: list[list[str]] = []

    done = langfuse_db.move(env, _stopped(calls))

    assert done.moved, done.reason
    assert (done.tables, done.rows) == (5, 7)
    assert calls == [["langfuse-web", "langfuse-worker"]], "Langfuse stopped before the copy"
    assert _rows(db, "langfuse") == before
    assert read_env_file(env)["LANGFUSE_DATABASE_URL"] == URL.format("langfuse"), "the password kept"
    assert _rows(db, "postgres") == before, "the tables stay in postgres, for going back"
    db.sql("INSERT INTO prompts (project_id, prompt) VALUES ('p1', 'c')", "langfuse")
    assert db.sql("SELECT max(id) FROM prompts", "langfuse") == "3", "the sequence moved along"
    again = langfuse_db.move(env, _stopped(calls))
    assert again == langfuse_db.Moved(), "once: nothing to do the second time"


@needs_docker
def test_after_the_move_a_backup_holds_langfuse_and_a_restore_brings_it_back(
    db: SimpleNamespace, tmp_path: Path,
) -> None:
    _langfuse_in_postgres(db)
    env = _env(tmp_path, URL.format("postgres"))
    assert backup.existing_databases("docker") == ["langfuse"], "postgres is left out, langfuse empty"
    assert langfuse_db.move(env, _stopped([])).moved
    kept = _rows(db, "langfuse")

    folder = tmp_path / "backup"
    folder.mkdir()
    dumps = backup.dump_databases(folder)
    assert dumps == {"langfuse": "langfuse.dump"}
    backup.write_manifest(folder, databases=dumps, env_snapshot="", volumes=[], migration_heads={},
                          kind="manual", release="0.7.1")
    db.sql("DELETE FROM api_keys; DELETE FROM prompts; DELETE FROM users;", "langfuse")

    manifest = backup.read_manifest(folder)
    assert manifest is not None
    with (tmp_path / "restore.log").open("wb") as log:
        prepared = backup.prepare_restore(
            SimpleNamespace(env_file=None, project_dir=tmp_path), folder, manifest, log,  # type: ignore[arg-type]
            db_only=True, work_parent=tmp_path,
        )
    swapped = restore_swap.swap_in(prepared.names, prepared.live)
    assert restore_swap.drop_old(swapped) == []

    assert _rows(db, "langfuse") == kept, "users, projects, API keys and prompts are back"


@needs_docker
def test_a_langfuse_database_with_tables_is_never_replaced(db: SimpleNamespace, tmp_path: Path) -> None:
    _langfuse_in_postgres(db)
    db.sql("CREATE TABLE someone_elses (id int); INSERT INTO someone_elses VALUES (1);", "langfuse")
    env = _env(tmp_path, URL.format("postgres"))
    calls: list[list[str]] = []

    done = langfuse_db.move(env, _stopped(calls))

    assert not done.moved and "not empty" in done.reason
    assert calls == [], "Langfuse not even stopped"
    assert db.sql("SELECT count(*) FROM someone_elses", "langfuse") == "1"
    assert read_env_file(env)["LANGFUSE_DATABASE_URL"] == URL.format("postgres")


@needs_docker
def test_a_copy_that_differs_leaves_langfuse_where_it_was(
    db: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _langfuse_in_postgres(db)
    env = _env(tmp_path, URL.format("postgres"))
    real = langfuse_db.table_counts

    def one_row_short(name: str, docker: str = "docker") -> dict[str, int]:
        counts = real(name, docker)
        if name == "langfuse":
            counts["public.users"] -= 1
        return counts

    monkeypatch.setattr(langfuse_db, "table_counts", one_row_short)

    done = langfuse_db.move(env, _stopped([]))

    assert not done.moved and "public.users" in done.reason
    assert read_env_file(env)["LANGFUSE_DATABASE_URL"] == URL.format("postgres")
    assert db.sql(langfuse_db.USER_RELATIONS, "langfuse") == "0", "langfuse empty again"
    assert "_prisma_migrations" in _rows(db, "postgres")


@needs_docker
def test_a_restore_error_leaves_langfuse_where_it_was(db: SimpleNamespace, tmp_path: Path) -> None:
    _langfuse_in_postgres(db)
    env = _env(tmp_path, URL.format("postgres"))
    real_run = subprocess.run

    def failing_restore(args: Any, *rest: Any, **kwargs: Any) -> Any:
        """pg_restore pointed at a database that does not exist: it fails as a real error would."""
        if isinstance(args, list) and "pg_restore" in args:
            args = [*args[:-1], "no_such_database"]
        return real_run(args, *rest, **kwargs)

    subprocess.run = failing_restore  # type: ignore[assignment]
    try:
        done = langfuse_db.move(env, _stopped([]))
    finally:
        subprocess.run = real_run  # type: ignore[assignment]

    assert not done.moved and "pg_restore" in done.reason
    assert read_env_file(env)["LANGFUSE_DATABASE_URL"] == URL.format("postgres")
    assert db.sql(langfuse_db.USER_RELATIONS, "langfuse") == "0"


@needs_docker
def test_langfuse_that_could_not_be_stopped_is_not_copied(db: SimpleNamespace, tmp_path: Path) -> None:
    _langfuse_in_postgres(db)
    env = _env(tmp_path, URL.format("postgres"))

    done = langfuse_db.move(env, _stopped([], code=1))

    assert not done.moved and "stopped" in done.reason
    assert db.sql(langfuse_db.USER_RELATIONS, "langfuse") == "0"
    assert read_env_file(env)["LANGFUSE_DATABASE_URL"] == URL.format("postgres")


@needs_docker
def test_a_langfuse_that_never_started_gets_its_own_database(db: SimpleNamespace, tmp_path: Path) -> None:
    db.sql("DROP DATABASE langfuse")
    env = _env(tmp_path, URL.format("postgres"))
    calls: list[list[str]] = []

    done = langfuse_db.move(env, _stopped(calls))

    assert done.moved and done.tables == 0 and calls == []
    assert "langfuse" in restore_swap.databases()
    assert read_env_file(env)["LANGFUSE_DATABASE_URL"] == URL.format("langfuse")
