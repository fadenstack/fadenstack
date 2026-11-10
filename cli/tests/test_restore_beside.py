"""``faden restore`` makes each database exactly the backup, on a real Postgres.

The restore piped each dump into ``pg_restore --clean --if-exists`` on the live database, which drops only what
the dump holds: on a test server (0.6.0 → 0.7.0rc1 → back, 10 November 2026) 0.7.0's six tables stayed, and
since one of them held foreign keys into ``user`` and ``agents``, those two could not be dropped and kept rows
made after the backup. pg_restore's error was shown as a warning cut to 200 characters; then "Restore complete".

Now every database is restored beside the live one (``<db>__restore``), swapped in by name in one transaction,
and the old one dropped once the services are healthy; a pg_restore error, an unhealthy start or too little room
each leave the install as it was. These tests run Postgres in a container of their own (the install's image,
``pgvector/pgvector:pg17``) and talk to it as the restore does, through ``docker exec``; nothing else is reached.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import restore
from faden_cli.core import backend_tool, backup, env_gen, maintenance, provenance_keys, restore_swap

IMAGE = "pgvector/pgvector:pg17"
REAL_RUN = subprocess.run  # before conftest's guard wraps it for each test


def _docker_ready() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    return REAL_RUN([docker, "info"], capture_output=True, check=False).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="needs Docker, for a real Postgres")


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    """A Postgres of the install's image, only for these tests, removed afterwards. Its data on a tmpfs of 4 GB
    (a limit; it takes only what is written): the restore keeps 1 GB free beyond its copies."""
    name = f"faden-cli-restore-test-{secrets.token_hex(4)}"
    started = REAL_RUN(
        ["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=test-only",
         "--tmpfs", "/var/lib/postgresql/data:rw,size=4g", IMAGE],
        capture_output=True, check=False,
    )
    assert started.returncode == 0, started.stderr.decode(errors="replace")
    try:
        for _ in range(60):
            ready = REAL_RUN(["docker", "exec", name, "pg_isready", "-U", "postgres", "-q"], check=False)
            if ready.returncode == 0:
                # pg_isready answers during the image's first-start initdb too; wait for the real server.
                if REAL_RUN(["docker", "exec", name, "psql", "-U", "postgres", "-tAc", "SELECT 1"],
                            capture_output=True, check=False).returncode == 0:
                    break
            time.sleep(1)
        else:
            pytest.fail("the test Postgres did not start")
        yield name
    finally:
        REAL_RUN(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.fixture
def db(pg: str, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The restore pointed at the test Postgres; a ``docker exec`` anywhere else still fails the test."""

    def only_ours(args: Any, *rest: Any, **kwargs: Any) -> Any:
        if isinstance(args, (list, tuple)) and args and "docker" in str(args[0]).lower() and "exec" in args:
            assert pg in args, f"a docker exec outside the test Postgres: {args!r}"
        return REAL_RUN(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", only_ours)
    monkeypatch.setattr(restore_swap, "POSTGRES_CONTAINER", pg)
    for name in _sql("SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'",
                     pg=pg).split():
        _sql(f'DROP DATABASE "{name}" WITH (FORCE)', pg=pg)
    return SimpleNamespace(name=pg, sql=lambda text, d="postgres": _sql(text, pg=pg, db=d))


def _sql(text: str, *, pg: str, db: str = "postgres") -> str:
    proc = REAL_RUN(
        ["docker", "exec", "-i", pg, "psql", "-U", "postgres", "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1",
         "-d", db],
        input=text.encode(), capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    return proc.stdout.decode().strip()


def _text(result: Any) -> str:
    """The command's output with rich's line wrapping undone."""
    return " ".join(result.output.split())


def _databases(db: SimpleNamespace) -> list[str]:
    return db.sql("SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1").split()


def _content(db: SimpleNamespace, name: str) -> dict[str, list[str]]:
    """Every table of *name* with its rows, as text, sorted: what "exactly the backup" compares."""
    tables = db.sql(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1", name
    ).split()
    return {t: db.sql(f'SELECT t::text FROM "{t}" t ORDER BY 1', name).splitlines() for t in tables}


def _backend_060(db: SimpleNamespace, name: str = "faden_backend") -> None:
    """The shape the test server had: user and agents, rows in both."""
    db.sql(f"CREATE DATABASE {name}")
    db.sql(
        "CREATE EXTENSION IF NOT EXISTS vector;"
        'CREATE TABLE "user" (id int PRIMARY KEY, email text NOT NULL);'
        "CREATE TABLE agents (id int PRIMARY KEY, slug text NOT NULL, created_by int REFERENCES \"user\"(id));"
        "CREATE TABLE alembic_version (version_num text PRIMARY KEY);"
        "INSERT INTO \"user\" VALUES (1, 'admin@example.com'), (2, 'sdk.tester@example.com');"
        "INSERT INTO agents VALUES (1, 'rel-files', 1);"
        "INSERT INTO alembic_version VALUES ('u6t3amtr33');",
        name,
    )


def _gateway_060(db: SimpleNamespace, name: str = "faden_gateway") -> None:
    db.sql(f"CREATE DATABASE {name}")
    db.sql(
        "CREATE TABLE usage_daily (day date PRIMARY KEY, calls int);"
        "INSERT INTO usage_daily VALUES ('2026-11-10', 3);"
        "CREATE TABLE alembic_version (version_num text PRIMARY KEY);"
        "INSERT INTO alembic_version VALUES ('u5ag3ers');",
        name,
    )


def _upgrade_to_070(db: SimpleNamespace) -> None:
    """What 0.7.0's migrations and a day on it add: a table with foreign keys into user and agents, an agent made
    after the backup, gateway tables, new heads."""
    db.sql(
        "CREATE TABLE bypass_step_ups (id int PRIMARY KEY, user_id int REFERENCES \"user\"(id), "
        "agent_id int REFERENCES agents(id));"
        "INSERT INTO agents VALUES (2, 'sdk-livecheck', 1);"
        "INSERT INTO bypass_step_ups VALUES (1, 2, 2);"
        "UPDATE alembic_version SET version_num = 'st3pup0';",
        "faden_backend",
    )
    db.sql(
        "CREATE TABLE approval_grant (id int PRIMARY KEY);"
        "CREATE TABLE approval_grant_call (id int PRIMARY KEY, grant_id int REFERENCES approval_grant(id));"
        "UPDATE alembic_version SET version_num = 'st3pup';",
        "faden_gateway",
    )


def _backup(db: SimpleNamespace, folder: Path, names: list[str], *, heads: dict[str, str] | None = None,
            release: str = "0.6.0") -> dict[str, Any]:
    """A backup as ``faden backup`` writes it: a custom-format dump per database and the manifest."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        proc = REAL_RUN(["docker", "exec", db.name, "pg_dump", "-U", "postgres", "-Fc", name],
                        capture_output=True, check=False)
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")
        (folder / f"{name}.dump").write_bytes(proc.stdout)
    backup.write_manifest(
        folder, databases={n: f"{n}.dump" for n in names}, env_snapshot="", volumes=[],
        migration_heads=heads or {}, kind="upgrade", release=release,
    )
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


def _prepare(tmp_path: Path, folder: Path, manifest: dict[str, Any]) -> tuple[backup.Prepared, Path]:
    log = tmp_path / "restore.log"
    ctx = SimpleNamespace(env_file=None, project_dir=tmp_path)
    with log.open("wb") as out:
        prepared = backup.prepare_restore(ctx, folder, manifest, out, db_only=True, work_parent=tmp_path)  # type: ignore[arg-type]
    return prepared, log


# ── The databases ─────────────────────────────────────────────────


def test_the_swap_makes_each_database_exactly_the_backup(db: SimpleNamespace, tmp_path: Path) -> None:
    _backend_060(db)
    _gateway_060(db)
    manifest = _backup(db, tmp_path / "b", ["faden_backend", "faden_gateway"],
                       heads={"faden_backend": "u6t3amtr33", "faden_gateway": "u5ag3ers"})
    expected = {n: _content(db, n) for n in ("faden_backend", "faden_gateway")}
    _upgrade_to_070(db)

    prepared, log = _prepare(tmp_path, tmp_path / "b", manifest)
    assert sorted(prepared.names) == ["faden_backend", "faden_gateway"]
    assert set(_databases(db)) == {"faden_backend", "faden_gateway", "faden_backend__restore",
                                   "faden_gateway__restore"}
    assert _content(db, "faden_backend")["agents"][-1].endswith("sdk-livecheck,1)"), "the live ones untouched so far"

    swapped = restore_swap.swap_in(prepared.names, prepared.live)

    for name in ("faden_backend", "faden_gateway"):
        assert _content(db, name) == expected[name], f"{name} is exactly the backup"
    assert "bypass_step_ups" not in _content(db, "faden_backend")
    assert set(_databases(db)) == {"faden_backend", "faden_gateway", "faden_backend__old", "faden_gateway__old"}
    assert "bypass_step_ups" in _content(db, "faden_backend__old"), "the old one kept until the start is healthy"
    assert restore_swap.drop_old(swapped) == []
    assert _databases(db) == ["faden_backend", "faden_gateway"]
    text = log.read_text(encoding="utf-8")
    assert "pg_restore exit code 0" in text and "processing data for table" in text, "the whole output, verbose"


def test_a_pg_restore_failure_leaves_the_live_databases_and_no_copy(db: SimpleNamespace, tmp_path: Path) -> None:
    _backend_060(db)
    _gateway_060(db)
    manifest = _backup(db, tmp_path / "b", ["faden_backend", "faden_gateway"])
    _upgrade_to_070(db)
    live = {n: _content(db, n) for n in ("faden_backend", "faden_gateway")}
    # The second dump cut short after its checksum was taken (a backup damaged before it was checksummed): the
    # first database restores, the second fails inside pg_restore.
    dump = tmp_path / "b" / "faden_gateway.dump"
    dump.write_bytes(dump.read_bytes()[: dump.stat().st_size * 2 // 3])

    with pytest.raises(restore_swap.RestoreFailed, match="pg_restore failed for faden_gateway"):
        _prepare(tmp_path, tmp_path / "b", manifest)

    assert _databases(db) == ["faden_backend", "faden_gateway"], "no copy left, the first one's neither"
    assert {n: _content(db, n) for n in live} == live, "the live databases untouched"
    text = (tmp_path / "restore.log").read_text(encoding="utf-8")
    assert "pg_restore exit code 0" in text, "faden_backend's copy was made, then dropped"
    assert "error" in text.lower() and "faden_gateway: pg_restore exit code 1" in text


def test_every_database_of_the_backup_is_restored_also_with_an_empty_head(db: SimpleNamespace, tmp_path: Path) -> None:
    _backend_060(db)
    db.sql("CREATE DATABASE rag")
    db.sql("CREATE EXTENSION IF NOT EXISTS vector; CREATE TABLE chunks (id int, v vector(3));"
           "INSERT INTO chunks VALUES (1, '[1,2,3]');", "rag")
    db.sql("CREATE DATABASE langfuse")
    manifest = _backup(db, tmp_path / "b", ["faden_backend", "rag", "langfuse"],
                       heads={"faden_backend": "u6t3amtr33", "rag": "", "langfuse": ""})
    expected = {n: _content(db, n) for n in ("faden_backend", "rag", "langfuse")}
    db.sql("DROP DATABASE langfuse")  # the install no longer has it: the backup's comes back all the same
    db.sql("CREATE DATABASE pii")  # one the backup does not hold: left as it is
    db.sql("DELETE FROM chunks", "rag")

    prepared, _ = _prepare(tmp_path, tmp_path / "b", manifest)
    swapped = restore_swap.swap_in(prepared.names, prepared.live)
    restore_swap.drop_old(swapped)

    assert prepared.not_in_backup == ["pii"]
    assert [s.had_live for s in swapped] == [True, True, False]
    assert {n: _content(db, n) for n in expected} == expected
    assert _databases(db) == ["faden_backend", "langfuse", "pii", "rag"]


def test_the_copy_is_made_like_the_live_database(db: SimpleNamespace, tmp_path: Path) -> None:
    """Owner, privileges and settings belong to the database, not to its dump: the copy takes the live one's."""
    _backend_060(db)
    db.sql("CREATE ROLE reader NOLOGIN; CREATE ROLE keeper NOLOGIN;"
           "ALTER DATABASE faden_backend OWNER TO keeper;"
           "REVOKE ALL ON DATABASE faden_backend FROM PUBLIC; GRANT CONNECT ON DATABASE faden_backend TO reader;"
           "ALTER DATABASE faden_backend SET work_mem = '8MB';")
    manifest = _backup(db, tmp_path / "b", ["faden_backend"])

    prepared, _ = _prepare(tmp_path, tmp_path / "b", manifest)
    restore_swap.drop_old(restore_swap.swap_in(prepared.names, prepared.live))

    row = db.sql("SELECT pg_get_userbyid(datdba), datacl::text FROM pg_database WHERE datname = 'faden_backend'")
    assert row.startswith("keeper|") and "reader=c/keeper" in row and "=Tc/" not in row, row
    assert db.sql("SELECT setconfig::text FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase "
                  "WHERE d.datname = 'faden_backend'") == "{work_mem=8MB}"


def test_leftovers_of_an_earlier_restore(db: SimpleNamespace, tmp_path: Path) -> None:
    _backend_060(db)
    manifest = _backup(db, tmp_path / "b", ["faden_backend"])
    db.sql("CREATE DATABASE faden_backend__restore")  # a copy an interrupted restore left: dropped, then made again
    prepared, _ = _prepare(tmp_path, tmp_path / "b", manifest)
    assert prepared.dropped == ["faden_backend__restore"]
    restore_swap.swap_in(prepared.names, prepared.live)  # and its __old left behind, as by a crash before the drop

    with pytest.raises(restore_swap.RestoreRefused, match=r"faden_backend__old.*Nothing was changed"):
        _prepare(tmp_path, tmp_path / "b", manifest)
    assert set(_databases(db)) == {"faden_backend", "faden_backend__old"}


def test_names_and_files_out_of_the_backup_are_refused(db: SimpleNamespace, tmp_path: Path) -> None:
    folder = tmp_path / "b"
    folder.mkdir()
    (folder / "x.dump").write_bytes(b"")
    for name in ('x"; DROP DATABASE faden_backend; --', "Faden", "postgres", "a" * 49):
        with pytest.raises(restore_swap.RestoreRefused, match="does not take"):
            _prepare(tmp_path, folder, {"databases": {name: {"file": "x.dump"}}})
    for file in ("../x.dump", "..", "sub/x.dump", "..\\x.dump"):
        with pytest.raises(restore_swap.RestoreRefused, match="not a file of the backup"):
            _prepare(tmp_path, folder, {"databases": {"faden_backend": {"file": file}}})
    assert _databases(db) == []


def test_the_restore_refuses_without_room(db: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _backend_060(db)
    manifest = _backup(db, tmp_path / "b", ["faden_backend"])
    filesystem, _ = restore_swap.data_free()
    # Exactly too little: what the copy and the reserve need, less one byte.
    need = backup.disk_need(backup.dump_files(tmp_path / "b", manifest), restore_swap.databases(), [],
                            SimpleNamespace(env_file=None), tmp_path)  # type: ignore[arg-type]
    monkeypatch.setattr(restore_swap, "data_free", lambda: (filesystem, need.databases + need.reserve - 1))

    with pytest.raises(restore_swap.RestoreRefused, match=r"Not enough room.*Nothing was changed"):
        _prepare(tmp_path, tmp_path / "b", manifest)
    assert _databases(db) == ["faden_backend"], "no copy was started"

    monkeypatch.setattr(restore_swap, "data_free", lambda: (filesystem, need.databases + need.reserve))
    prepared, _ = _prepare(tmp_path, tmp_path / "b", manifest)
    assert prepared.names == ["faden_backend"]


# ── The whole command ─────────────────────────────────────────────


@pytest.fixture
def install(db: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An install whose databases are the test Postgres': compose, RabbitMQ and the backend tool answered here.

    ``state.healthy`` decides whether starting everything works; every compose call is in ``state.calls``,
    with whether the maintenance page was up.
    """
    root = tmp_path / "install"
    (root / "tls").mkdir(parents=True)
    (root / "rabbitmq").mkdir()
    (root / ".env").write_text("VERSION=0.7.0rc1\nPOSTGRES_PASSWORD=live\n", encoding="utf-8")
    (root / "tls" / "edge.crt").write_text("live certificate", encoding="utf-8")
    (root / "rabbitmq" / "definitions.json").write_text('{"users": "live"}', encoding="utf-8")
    ctx = SimpleNamespace(project_dir=root, env_file=root / ".env", profiles=[])
    state = SimpleNamespace(root=root, healthy=True, calls=[], volumes=[])

    def compose(what: str):
        def inner(_ctx: object, *, services: list[str] | None = None, **_: object) -> int:
            state.calls.append((what, services, maintenance.state(root) is not None))
            if what == "up" and services is None and not state.healthy:
                return 1
            return 0
        return inner

    def written_from_env(shared_dir: Path, env: dict[str, str], profiles: object) -> None:
        (shared_dir / "rabbitmq" / "definitions.json").write_text(
            json.dumps({"users": env.get("POSTGRES_PASSWORD")}), encoding="utf-8")

    monkeypatch.setattr(restore, "load_config", lambda: SimpleNamespace(profiles=[], install_dir=str(root)))
    monkeypatch.setattr(restore, "build_context_from_config", lambda cfg: ctx)
    monkeypatch.setattr(restore, "service_names", lambda ctx: ["nginx", "postgres", "faden-backend", "faden-gateway"])
    monkeypatch.setattr(restore, "compose_stop", compose("stop"))
    monkeypatch.setattr(restore, "compose_up", compose("up"))
    monkeypatch.setattr(restore, "compose_restart", compose("restart"))
    monkeypatch.setattr(restore, "compose_ps", lambda ctx: [SimpleNamespace(
        service="faden-backend", name="faden-backend", state="running", status="running", health="unhealthy",
        exit_code=0)])
    monkeypatch.setattr(restore, "_sync_postgres_password", lambda ctx, env: state.calls.append(("password", None, True)))
    monkeypatch.setattr(restore, "_regenerate_rmq_definitions", written_from_env)
    monkeypatch.setattr(env_gen, "fill_required_env", lambda *a, **k: [])
    monkeypatch.setattr(provenance_keys, "ensure_step", lambda *a, **k: None)
    monkeypatch.setattr(backend_tool, "record_event", lambda *a, **k: True)

    # The volumes go through helper containers (their own tests): here a volume is a folder of the test.
    vol = tmp_path / "volume-faden_audit"
    vol.mkdir()
    (vol / "trail").write_text("live trail", encoding="utf-8")

    def snapshot(folder: Path, *, volumes: list[str], **_: object) -> list[str]:
        for name in volumes:
            (folder / f"{name}.tar.gz").write_text((vol / "trail").read_text(encoding="utf-8"), encoding="utf-8")
        state.volumes.append(("kept", volumes))
        return [f"{name}.tar.gz" for name in volumes]

    def put(folder: Path, manifest: dict[str, Any], *, failed: list[str] | None = None, **_: object) -> list[str]:
        names = [a.removesuffix(".tar.gz") for a in manifest["volumes"]]
        for archive in manifest["volumes"]:
            (vol / "trail").write_text((folder / archive).read_text(encoding="utf-8"), encoding="utf-8")
        state.volumes.append(("restored", folder.name))
        return names

    monkeypatch.setattr(backup, "volumes_replaced", lambda manifest, ctx: ["faden_audit"])
    monkeypatch.setattr(backup, "_volume_bytes", lambda volumes, ctx: 1000)
    monkeypatch.setattr(backup, "snapshot_volumes", snapshot)
    monkeypatch.setattr(backup, "restore_volumes", put)
    monkeypatch.setattr(restore, "restore_volumes", put)
    state.trail = vol / "trail"
    return state


def _full_backup(db: SimpleNamespace, tmp_path: Path) -> Path:
    """faden_backend and faden_gateway as on 0.6.0, with the backup's .env, certificate and audit trail."""
    _backend_060(db)
    _gateway_060(db)
    folder = tmp_path / "backup"
    _backup(db, folder, ["faden_backend", "faden_gateway"])
    (folder / ".env.bak").write_text("VERSION=0.6.0\nPOSTGRES_PASSWORD=backup\n", encoding="utf-8")
    (folder / "tls-material" / "tls").mkdir(parents=True)
    (folder / "tls-material" / "tls" / "edge.crt").write_text("backup certificate", encoding="utf-8")
    (folder / "faden_audit.tar.gz").write_text("backup trail", encoding="utf-8")
    backup.write_manifest(
        folder, databases={"faden_backend": "faden_backend.dump", "faden_gateway": "faden_gateway.dump"},
        env_snapshot=".env.bak", volumes=["faden_audit.tar.gz"], migration_heads={"faden_backend": "u6t3amtr33"},
        tls_material=["tls/edge.crt"], kind="upgrade", release="0.6.0",
    )
    return folder


def test_a_healthy_restore_swaps_in_drops_the_old_and_says_complete(
    db: SimpleNamespace, install: SimpleNamespace, tmp_path: Path
) -> None:
    folder = _full_backup(db, tmp_path)
    expected = {n: _content(db, n) for n in ("faden_backend", "faden_gateway")}
    _upgrade_to_070(db)

    result = CliRunner().invoke(restore.restore_cmd, [str(folder), "-y"])

    assert result.exit_code == 0, result.output
    assert "Backup of release 0.6.0, taken by faden" in _text(result)
    assert "Restore complete." in _text(result)
    assert {n: _content(db, n) for n in expected} == expected
    assert _databases(db) == ["faden_backend", "faden_gateway"], "no __old, no __restore"
    assert (install.root / ".env").read_text(encoding="utf-8").startswith("VERSION=0.6.0")
    assert (install.root / "tls" / "edge.crt").read_text(encoding="utf-8") == "backup certificate"
    assert install.trail.read_text(encoding="utf-8") == "backup trail"
    assert '"backup"' in (install.root / "rabbitmq" / "definitions.json").read_text(encoding="utf-8")
    assert not list(install.root.glob(".restore-*")), "the kept copies removed"
    logs = list((install.root / "logs").glob("restore-*.log"))
    assert len(logs) == 1 and str(logs[0]) in result.output.replace("\n", "").replace(" ", "")
    if sys.platform != "win32":
        assert logs[0].stat().st_mode & 0o777 == 0o600
    # The maintenance page from the first stop to the last start; the restore beside ran without it.
    calls = [(what, services, down) for what, services, down in install.calls if what != "password"]
    first_stop = next(i for i, (what, _, _) in enumerate(calls) if what == "stop")
    assert all(not down for _, _, down in calls[:first_stop])
    assert all(down for _, _, down in calls[first_stop:])
    assert ["nginx", "postgres"] == [s for s in ("nginx", "postgres") if s not in calls[first_stop][1]]


def test_an_unhealthy_start_swaps_everything_back(db: SimpleNamespace, install: SimpleNamespace, tmp_path: Path) -> None:
    folder = _full_backup(db, tmp_path)
    _upgrade_to_070(db)
    before = {n: _content(db, n) for n in ("faden_backend", "faden_gateway")}
    env_before = (install.root / ".env").read_bytes()
    install.healthy = False

    result = CliRunner().invoke(restore.restore_cmd, [str(folder), "-y"])

    assert result.exit_code == 1, result.output
    assert "Restore complete" not in _text(result)
    assert "did not become healthy" in _text(result) and "faden-backend (unhealthy)" in _text(result)
    assert "Everything the restore changed went back" in _text(result)
    assert {n: _content(db, n) for n in before} == before, "the databases before the restore are live again"
    assert _databases(db) == ["faden_backend", "faden_gateway"], "the restored copies dropped"
    assert (install.root / ".env").read_bytes() == env_before
    assert (install.root / "tls" / "edge.crt").read_text(encoding="utf-8") == "live certificate"
    assert install.trail.read_text(encoding="utf-8") == "live trail"
    assert '"live"' in (install.root / "rabbitmq" / "definitions.json").read_text(encoding="utf-8")
    assert not list(install.root.glob(".restore-*"))
    starts = [down for what, services, down in install.calls if what == "up" and services is None]
    assert starts == [True, True], "started with the backup's state, then again with the one before"


def test_a_pg_restore_failure_changes_nothing_and_stops_nothing(
    db: SimpleNamespace, install: SimpleNamespace, tmp_path: Path
) -> None:
    folder = _full_backup(db, tmp_path)
    _upgrade_to_070(db)
    before = {n: _content(db, n) for n in ("faden_backend", "faden_gateway")}
    dump = folder / "faden_backend.dump"
    dump.write_bytes(dump.read_bytes()[: dump.stat().st_size // 2])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["databases"]["faden_backend"]["sha256"] = backup._sha256(dump)  # past the checksum: inside pg_restore
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(restore.restore_cmd, [str(folder), "-y"])

    assert result.exit_code == 1, result.output
    assert "pg_restore failed for faden_backend" in _text(result) and "Nothing was changed" in _text(result)
    assert {n: _content(db, n) for n in before} == before
    assert _databases(db) == ["faden_backend", "faden_gateway"]
    assert [what for what, _, _ in install.calls] == ["up"], "only Postgres made sure of; nothing stopped"
    assert (install.root / ".env").read_text(encoding="utf-8").startswith("VERSION=0.7.0rc1")


def test_the_command_refuses_without_room_and_stops_nothing(
    db: SimpleNamespace, install: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _full_backup(db, tmp_path)
    monkeypatch.setattr(restore_swap, "data_free", lambda: ("/dev/test", 10 * 1024 * 1024))

    result = CliRunner().invoke(restore.restore_cmd, [str(folder), "-y"])

    assert result.exit_code == 1
    assert "Not enough room" in _text(result) and "Nothing was changed" in _text(result)
    assert [what for what, _, _ in install.calls] == ["up"]
    assert _databases(db) == ["faden_backend", "faden_gateway"]
