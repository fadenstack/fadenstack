"""Backup and restore helpers for Fadenstack databases and volumes.

Shared by ``faden backup``, ``faden restore``, and
``faden upgrade``.  All database operations run via
``docker exec`` against the running ``faden-postgres`` container —
no direct network connection is needed.

A backup holds every database and every secret of the install, so it is
readable by the user who made it only: its directory 0700, its files 0600,
the volume archives too (the CLI writes them from the helper container's
output, rather than the container writing them as root).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from faden_cli import __version__
from faden_cli.core.compose import ComposeContext, up as compose_up
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.private_files import PRIVATE_DIR, open_private, private_dir, write_private
from faden_cli.core.registry import DATABASES, POSTGRES_CONTAINER

# Named Docker volumes that can be included in a backup, by their name in the compose file. Not among them:
# Postgres's data volume, because the dumps are the databases' backup, a copy of a running data directory is
# not consistent, and writing one back under the Postgres a restore runs would break it; and the models,
# which can be downloaded again and run to hundreds of gigabytes.
BACKUP_VOLUMES: list[str] = [
    "faden_rag_lite",
    "minio_data",
    "ch_data",
    "ch_logs",
    "loki_data",
    "grafana_data",
]

#: Never written back by a restore, whatever an older backup holds.
_NEVER_RESTORED = frozenset({"pg_data"})

# Alembic migrator containers (service name → database).
_MIGRATOR_SERVICES: dict[str, str] = {
    "faden-backend-migrator": "faden_backend",
    "faden-gateway-migrator": "faden_gateway",
    "faden-mcp-migrator": "faden_mcp",
    "faden-skills-migrator": "faden_skills",
    "faden-pii-migrator": "pii",
}


# ── Result types ──────────────────────────────────────────────────


@dataclass
class BackupResult:
    """Outcome of a backup operation."""

    backup_dir: Path
    databases: dict[str, str] = field(default_factory=dict)  # db → dump file
    env_snapshot: str = ""
    volumes: list[str] = field(default_factory=list)
    tls_material: list[str] = field(default_factory=list)
    manifest_path: Path | None = None
    ok: bool = True
    errors: list[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    """Outcome of a restore operation."""

    databases_restored: list[str] = field(default_factory=list)
    env_restored: bool = False
    volumes_restored: list[str] = field(default_factory=list)
    tls_restored: list[str] = field(default_factory=list)
    ok: bool = True
    errors: list[str] = field(default_factory=list)


# ── Checksums ─────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Database dump / restore ───────────────────────────────────────


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("docker not found on PATH")
    return docker


def existing_databases(docker: str) -> list[str] | None:
    """The databases the Postgres server holds, or ``None`` when it cannot say.

    A backup takes these rather than a fixed list: the list named ``pii``,
    absent where the PII module was never on (a failed dump, a warning), and
    missed ``rag``, which an install had -- so it was never backed up.
    """
    proc = subprocess.run(  # noqa: S603
        [
            docker, "exec", POSTGRES_CONTAINER, "psql", "-U", "postgres", "-tAc",
            "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    names = [line.strip() for line in proc.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    return names or None


def dump_databases(
    backup_dir: Path,
    *,
    databases: list[str] | None = None,
) -> dict[str, str]:
    """Dump each database with ``pg_dump -Fc`` via docker exec.

    Returns a mapping of *database name* → *dump filename* for
    databases that were successfully dumped.
    """
    docker = _docker_bin()
    targets = databases or existing_databases(docker) or list(DATABASES)
    results: dict[str, str] = {}

    for db in targets:
        dump_file = f"{db}.dump"
        dump_path = backup_dir / dump_file

        info(f"  Dumping database: {db}")
        proc = subprocess.run(  # noqa: S603
            [
                docker, "exec", POSTGRES_CONTAINER,
                "pg_dump", "-U", "postgres", "-Fc", db,
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            warning(f"  pg_dump failed for {db}: {stderr}")
            continue

        write_private(dump_path, proc.stdout)
        results[db] = dump_file

    return results


def restore_databases(
    backup_dir: Path,
    manifest: dict,
) -> list[str]:
    """Restore databases from custom-format dumps.

    Returns the list of database names that were successfully restored.
    """
    docker = _docker_bin()
    restored: list[str] = []
    db_section = manifest.get("databases", {})

    for db, meta in db_section.items():
        dump_file = meta.get("file", f"{db}.dump")
        dump_path = backup_dir / dump_file
        if not dump_path.exists():
            warning(f"  Dump file missing for {db}: {dump_file}")
            continue

        info(f"  Restoring database: {db}")

        # Read the dump file and pipe it into pg_restore via stdin.
        dump_bytes = dump_path.read_bytes()
        proc = subprocess.run(  # noqa: S603
            [
                docker, "exec", "-i", POSTGRES_CONTAINER,
                "pg_restore", "-U", "postgres",
                "--clean", "--if-exists", "-d", db,
            ],
            input=dump_bytes,
            capture_output=True,
            check=False,
        )
        # pg_restore may return non-zero for harmless warnings
        # (e.g. "role does not exist" during --clean).
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and "ERROR" in stderr.upper():
            warning(f"  pg_restore warnings for {db}: {stderr[:200]}")
        restored.append(db)

    return restored


# ── Migration heads ───────────────────────────────────────────────


def get_migration_heads(ctx: ComposeContext) -> dict[str, str]:
    """Collect current Alembic revision heads from migrator containers.

    Runs ``alembic current`` inside each migrator service.  Returns a
    mapping of *database name* → *revision hash* (or empty string if
    the head could not be determined).
    """
    docker = _docker_bin()
    heads: dict[str, str] = {}

    for service, db in _MIGRATOR_SERVICES.items():
        proc = subprocess.run(  # noqa: S603
            [
                docker, "compose",
                *_compose_flags(ctx),
                "run", "--rm", "--no-deps", service,
                "alembic", "current",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        head = ""
        if proc.returncode == 0:
            # Output looks like: "a1b2c3d4e5f6 (head)"
            for line in proc.stdout.strip().splitlines():
                if "(head)" in line:
                    head = line.split()[0]
                    break
        heads[db] = head

    return heads


def _compose_flags(ctx: ComposeContext) -> list[str]:
    """Build raw compose flags from a ComposeContext (without 'docker compose')."""
    flags: list[str] = []
    for f in ctx.compose_files:
        flags.extend(["-f", str(f)])
    if ctx.env_file and Path(str(ctx.env_file)).exists():
        flags.extend(["--env-file", str(ctx.env_file)])
    if ctx.project_dir:
        flags.extend(["--project-directory", str(ctx.project_dir)])
    for p in ctx.profiles:
        flags.extend(["--profile", p])
    return flags


# ── .env snapshot ─────────────────────────────────────────────────


def snapshot_env(env_path: Path, backup_dir: Path) -> str:
    """Copy the .env file into the backup directory.

    Returns the filename inside backup_dir, or empty string on failure.
    """
    if not env_path.exists():
        warning("  No .env file found — skipping env snapshot.")
        return ""
    dest = backup_dir / ".env.bak"
    write_private(dest, env_path.read_bytes())
    return ".env.bak"


def restore_env(backup_dir: Path, env_path: Path) -> bool:
    """Restore the .env snapshot from a backup, readable by its owner only."""
    src = backup_dir / ".env.bak"
    if not src.exists():
        warning("  No .env snapshot in backup.")
        return False
    write_private(env_path, src.read_bytes())
    return True


# ── Volume snapshots ──────────────────────────────────────────────


def _compose_project(docker: str) -> str:
    """The compose project the running install belongs to, read from its Postgres container."""
    proc = subprocess.run(  # noqa: S603
        [
            docker, "inspect", "-f", '{{ index .Config.Labels "com.docker.compose.project" }}',
            POSTGRES_CONTAINER,
        ],
        capture_output=True,
        check=False,
    )
    return proc.stdout.decode("utf-8", errors="replace").strip() if proc.returncode == 0 else ""


def _compose_volume(docker: str, project: str, name: str) -> str | None:
    """The Docker volume behind the compose volume *name*, or ``None`` when the install has none.

    Compose names a volume after its project (``fadenstack_minio_data``). The bare name is another volume,
    one ``docker run -v`` creates empty on the spot: backups tarred that and restores filled it.
    """
    if not project:
        return None
    proc = subprocess.run(  # noqa: S603
        [
            docker, "volume", "ls", "-q",
            "--filter", f"label=com.docker.compose.project={project}",
            "--filter", f"label=com.docker.compose.volume={name}",
        ],
        capture_output=True,
        check=False,
    )
    lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
    found = [line.strip() for line in lines if line.strip()]
    return found[0] if proc.returncode == 0 and len(found) == 1 else None


def snapshot_volumes(backup_dir: Path, *, volumes: list[str] | None = None) -> list[str]:
    """Tar each named Docker volume into the backup directory.

    Uses a temporary ``alpine`` container to read from the volume mount. The archive carries the compose
    name (``minio_data.tar.gz``), so a restore finds the volume on whichever install it runs.
    Returns list of created archive filenames.

    The container writes the archive to its output and the CLI into the backup, readable by its owner
    only. Written by the container into a mounted backup directory, it was root's, 0644, for every
    local user to read.
    """
    docker = _docker_bin()
    project = _compose_project(docker)
    targets = volumes or list(BACKUP_VOLUMES)
    created: list[str] = []

    for vol in targets:
        source = _compose_volume(docker, project, vol)
        if source is None:
            warning(f"  No volume {vol} in this install; skipped")
            continue
        archive = f"{vol}.tar.gz"
        archive_path = backup_dir / archive
        info(f"  Snapshotting volume: {vol}")
        with open_private(archive_path) as out:
            proc = subprocess.run(  # noqa: S603
                [
                    docker, "run", "--rm",
                    "-v", f"{source}:/data:ro",
                    "alpine",
                    "tar", "czf", "-", "-C", "/data", ".",
                ],
                stdout=out,
                stderr=subprocess.PIPE,
                check=False,
            )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            warning(f"  Volume snapshot failed for {vol}: {stderr[:200]}")
            archive_path.unlink(missing_ok=True)
            continue
        created.append(archive)

    return created


def restore_volumes(backup_dir: Path, manifest: dict) -> list[str]:
    """Restore volume archives from a backup.

    Returns list of volume names that were restored.
    """
    docker = _docker_bin()
    project = _compose_project(docker)
    archives = manifest.get("volumes", [])
    restored: list[str] = []

    for archive in archives:
        archive_path = backup_dir / archive
        if not archive_path.exists():
            warning(f"  Volume archive missing: {archive}")
            continue
        # Derive volume name from archive filename (e.g. "minio_data.tar.gz" → "minio_data")
        vol = archive.removesuffix(".tar.gz")
        if vol in _NEVER_RESTORED:
            warning(f"  {vol} skipped: the databases come back from their dumps")
            continue
        target = _compose_volume(docker, project, vol)
        if target is None:
            warning(f"  No volume {vol} in this install; {archive} not restored")
            continue
        info(f"  Restoring volume: {vol}")
        # The archive goes in on the container's input: the backup is its owner's only, which a container's
        # root may not be (user namespaces), and a mount of it is not needed.
        with archive_path.open("rb") as archive_in:
            proc = subprocess.run(  # noqa: S603
                [
                    docker, "run", "--rm", "-i",
                    "-v", f"{target}:/data",
                    "alpine",
                    "sh", "-c", "find /data -mindepth 1 -delete && tar xzf - -C /data",
                ],
                stdin=archive_in,
                capture_output=True,
                check=False,
            )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            warning(f"  Volume restore failed for {vol}: {stderr[:200]}")
            continue
        restored.append(vol)

    return restored


# ── Manifest ──────────────────────────────────────────────────────


# ── Certificate material ──────────────────────────────────────────

#: What HTTPS and the machines' trust rest on, relative to the install:
#: the certificate served and its key, and the install's own authority with
#: its key. Without the authority, a server rebuilt from a backup makes a new
#: one, and every machine that followed it to HTTPS refuses to connect.
TLS_MATERIAL = (
    "tls/edge.crt",
    "tls/edge.key",
    "tls/edge-chain.pem",
    "tls/ca.crt",
    "tls-ca/ca.crt",
    "tls-ca/ca.key",
    "tls-ca/ca-previous.crt",
    "tls-ca/roots.pem",
    "tls-ca/source.json",
)
_TLS_BACKUP_DIR = "tls-material"


def snapshot_tls(install_dir: Path, backup_dir: Path) -> list[str]:
    """Copy the certificate material into the backup; the files copied, relative to the install."""
    copied: list[str] = []
    for rel in TLS_MATERIAL:
        src = install_dir / rel
        if not src.is_file():
            continue
        dest = backup_dir / _TLS_BACKUP_DIR / rel
        private_dir(dest.parent)
        write_private(dest, src.read_bytes())
        copied.append(rel)
    return copied


def _tls_mode(rel: str) -> int:
    """The mode a certificate file has in an install: keys (and where they came from) owner-only."""
    return 0o600 if rel.endswith((".key", "source.json")) else 0o644


def restore_tls(backup_dir: Path, install_dir: Path, files: list[str]) -> list[str]:
    """Put the certificate material back, keys readable by their owner only.

    Each file gets the mode it has in an install, not the backup's (where every file is its owner's only):
    the certificates are read by nginx and handed to the machines.
    """
    restored: list[str] = []
    for rel in files:
        if rel not in TLS_MATERIAL:
            continue  # only the files this CLI backs up, never a path out of the install
        src = backup_dir / _TLS_BACKUP_DIR / rel
        if not src.is_file():
            continue
        dest = install_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_private(dest, src.read_bytes(), mode=_tls_mode(rel))
        restored.append(rel)
    if any(rel.startswith("tls-ca/") for rel in restored):
        (install_dir / "tls-ca").chmod(PRIVATE_DIR)
    return restored


def write_manifest(
    backup_dir: Path,
    *,
    databases: dict[str, str],
    env_snapshot: str,
    volumes: list[str],
    migration_heads: dict[str, str],
    tls_material: list[str] | None = None,
) -> Path:
    """Write ``manifest.json`` with file checksums and metadata."""
    db_entries: dict[str, dict] = {}
    for db, dump_file in databases.items():
        dump_path = backup_dir / dump_file
        db_entries[db] = {
            "file": dump_file,
            "sha256": _sha256(dump_path) if dump_path.exists() else "",
            "migration_head": migration_heads.get(db, ""),
        }

    manifest = {
        "version": 1,
        "created_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "faden_version": __version__,
        "databases": db_entries,
        "env_snapshot": env_snapshot,
        "volumes": volumes,
        "tls_material": {
            rel: _sha256(backup_dir / _TLS_BACKUP_DIR / rel) for rel in (tls_material or [])
        },
    }

    manifest_path = backup_dir / "manifest.json"
    write_private(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return manifest_path


def read_manifest(backup_dir: Path) -> dict | None:
    """Read and parse ``manifest.json`` from a backup directory."""
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_backup(backup_dir: Path, manifest: dict) -> list[str]:
    """Verify database dump checksums against the manifest.

    Returns a list of error messages (empty if all OK).
    """
    errors: list[str] = []
    for db, meta in manifest.get("databases", {}).items():
        dump_file = meta.get("file", "")
        expected = meta.get("sha256", "")
        if not dump_file or not expected:
            continue
        dump_path = backup_dir / dump_file
        if not dump_path.exists():
            errors.append(f"Missing dump file: {dump_file}")
            continue
        actual = _sha256(dump_path)
        if actual != expected:
            errors.append(f"Checksum mismatch for {dump_file}: expected {expected[:12]}… got {actual[:12]}…")
    for rel, expected in (manifest.get("tls_material") or {}).items():
        path = backup_dir / _TLS_BACKUP_DIR / rel
        if not path.is_file():
            errors.append(f"Missing certificate file: {rel}")
        elif _sha256(path) != expected:
            errors.append(f"Checksum mismatch for {rel}")
    return errors


# ── Retention ─────────────────────────────────────────────────────


def rotate_backups(backup_root: Path, *, retain: int = 5) -> int:
    """Remove oldest backup directories beyond the retention count.

    Assumes backup directories are named with ISO timestamps so
    lexicographic sort == chronological sort.  Returns the number
    of directories removed.
    """
    if retain <= 0:
        return 0

    dirs = sorted(
        [d for d in backup_root.iterdir() if d.is_dir() and (d / "manifest.json").exists()],
        key=lambda d: d.name,
    )
    to_remove = dirs[:-retain] if len(dirs) > retain else []
    for d in to_remove:
        shutil.rmtree(d)
    return len(to_remove)


# ── Orchestrators ─────────────────────────────────────────────────


def create_backup(
    ctx: ComposeContext,
    *,
    output_dir: Path,
    include_volumes: bool = False,
    db_only: bool = False,
    retain: int = 5,
) -> BackupResult:
    """Full backup orchestration.

    1. Ensure postgres is running
    2. Create timestamped directory
    3. Dump databases
    4. Snapshot .env
    5. Optionally snapshot volumes
    6. Collect migration heads
    7. Write manifest
    8. Rotate old backups
    """
    # Owner-only, the directory and each backup in it: a backup holds every database and secret. A directory
    # of the operator's (--output-dir) that already exists keeps its mode; the backups in it are private.
    installs_own = ctx.project_dir is not None and (
        output_dir.resolve() == (Path(str(ctx.project_dir)) / "backups").resolve()
    )
    if installs_own or not output_dir.exists():
        private_dir(output_dir)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = private_dir(output_dir / timestamp)

    result = BackupResult(backup_dir=backup_dir)

    # 1. Ensure postgres is running
    info("Ensuring PostgreSQL is running…")
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    # 2. Dump databases
    console.print("\n[bold cyan]Dumping databases…[/bold cyan]")
    result.databases = dump_databases(backup_dir)
    if not result.databases:
        result.ok = False
        result.errors.append("No databases were dumped successfully.")
        return result

    # 3. Snapshot .env
    if not db_only and ctx.env_file:
        console.print("\n[bold cyan]Snapshotting .env…[/bold cyan]")
        result.env_snapshot = snapshot_env(Path(str(ctx.env_file)), backup_dir)

    # 3b. The certificate material: HTTPS and the machines' trust rest on it
    if not db_only and ctx.project_dir:
        result.tls_material = snapshot_tls(Path(str(ctx.project_dir)), backup_dir)
        if result.tls_material:
            count = len(result.tls_material)
            console.print(f"\n[bold cyan]Certificate material:[/bold cyan] {count} file(s)")

    # 4. Snapshot volumes
    if include_volumes and not db_only:
        console.print("\n[bold cyan]Snapshotting Docker volumes…[/bold cyan]")
        result.volumes = snapshot_volumes(backup_dir)

    # 5. Collect migration heads
    console.print("\n[bold cyan]Collecting migration heads…[/bold cyan]")
    heads = get_migration_heads(ctx)

    # 6. Write manifest
    console.print("\n[bold cyan]Writing manifest…[/bold cyan]")
    result.manifest_path = write_manifest(
        backup_dir,
        databases=result.databases,
        env_snapshot=result.env_snapshot,
        volumes=result.volumes,
        migration_heads=heads,
        tls_material=result.tls_material,
    )

    # 7. Rotate
    removed = rotate_backups(output_dir, retain=retain)
    if removed:
        info(f"Rotated {removed} old backup(s).")

    return result


def restore_backup(
    ctx: ComposeContext,
    *,
    backup_dir: Path,
    db_only: bool = False,
    skip_env: bool = False,
) -> RestoreResult:
    """Full restore orchestration.

    1. Read and verify manifest
    2. Ensure postgres is running
    3. Restore databases
    4. Restore .env
    5. Restore volumes
    """
    result = RestoreResult()

    manifest = read_manifest(backup_dir)
    if manifest is None:
        result.ok = False
        result.errors.append("No manifest.json found in backup directory.")
        return result

    # Verify checksums
    check_errors = verify_backup(backup_dir, manifest)
    if check_errors:
        result.ok = False
        result.errors.extend(check_errors)
        return result

    # 1. Ensure postgres is running
    info("Ensuring PostgreSQL is running…")
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    # 2. Restore databases
    console.print("\n[bold cyan]Restoring databases…[/bold cyan]")
    result.databases_restored = restore_databases(backup_dir, manifest)

    # 3. Restore .env
    if not db_only and not skip_env and manifest.get("env_snapshot") and ctx.env_file:
        console.print("\n[bold cyan]Restoring .env…[/bold cyan]")
        result.env_restored = restore_env(backup_dir, Path(str(ctx.env_file)))

    # 4. Restore volumes
    if not db_only and manifest.get("volumes"):
        console.print("\n[bold cyan]Restoring Docker volumes…[/bold cyan]")
        result.volumes_restored = restore_volumes(backup_dir, manifest)

    # 5. The certificate material, so the machines still trust this server
    if not db_only and manifest.get("tls_material") and ctx.project_dir:
        console.print("\n[bold cyan]Restoring the certificate material…[/bold cyan]")
        result.tls_restored = restore_tls(
            backup_dir, Path(str(ctx.project_dir)), list(manifest["tls_material"])
        )

    return result
