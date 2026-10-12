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
import sys
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
    "faden_chat_files",
    "minio_data",
    "ch_data",
    "ch_logs",
    "loki_data",
    "grafana_data",
]

#: The company data volumes docker-compose.yaml can put in a host directory instead: with FADEN_DATA_DIR set in
#: .env, the volume is that directory plus the subdirectory named here. Mirrors the mounts in the compose file.
DATA_DIR_VOLUMES: dict[str, str] = {"faden_chat_files": "chat-files"}

#: Where the gateway keeps chat attachments, and its container: files an older release left in the
#: container layer are carried into the volume before the container is replaced.
_GATEWAY_CONTAINER = "faden-gateway"
_CHAT_FILES_PATH = "/data/faden/chat-files"
_DEFAULT_PROJECT = "fadenstack"

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


#: What made a backup, recorded in its manifest. Retention counts each kind apart. A backup without a kind
#: (an older install's) is manual.
BACKUP_KINDS = ("manual", "nightly", "upgrade")
DEFAULT_KIND = "manual"


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


def data_dir_of(env_file: object) -> str:
    """FADEN_DATA_DIR from the install's .env: where the data volumes live on the host, or ``""`` for named volumes."""
    if not env_file or not Path(str(env_file)).exists():
        return ""
    from faden_cli.core.env_gen import read_env_file  # noqa: PLC0415

    return read_env_file(Path(str(env_file))).get("FADEN_DATA_DIR", "").strip().rstrip("/\\")


def keep_chat_files(env_file: object) -> None:
    """Before the gateway container is replaced: :func:`carry_over_chat_files` for the install with this ``.env``.

    When the files cannot be copied and verified, the command stops here with the reason and what to do: the
    old containers still run and the files are still in the old gateway container.
    """
    try:
        result = carry_over_chat_files(data_dir_of(env_file))
    except ChatFilesNotCarried as exc:
        error(str(exc))
        sys.exit(1)
    if result.status == "copied":
        success(
            f"Chat attachments copied from the old gateway container into the data volume and verified "
            f"({result.files} file(s), {_human_size(result.size)})."
        )


def _volume_source(docker: str, project: str, name: str, data_dir: str) -> str | None:
    """What ``docker run -v`` mounts for the compose volume *name*: its host directory, or the named volume."""
    if data_dir and name in DATA_DIR_VOLUMES:
        return f"{data_dir}/{DATA_DIR_VOLUMES[name]}"
    return _compose_volume(docker, project, name)


class ChatFilesNotCarried(RuntimeError):
    """The chat attachments in the old gateway container could not be copied and verified.

    The message says what failed and what to do. Raised before any container is replaced, so the files are
    where they were.
    """


@dataclass
class CarryResult:
    """What :func:`carry_over_chat_files` did: ``"copied"``, ``"present"`` (the old container has the mount) or
    ``"none"`` (nothing to carry), and how many files and bytes were copied and verified."""

    status: str
    files: int = 0
    size: int = 0


def _human_size(size: int) -> str:
    """*size* in bytes as the operator reads it: ``12.3 MB``."""
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{size} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{size} bytes"  # unreachable


#: Run in the helper container, once per file of the tar stream and with the file on stdin (GNU tar's
#: ``--to-command``): one line ``<sha256>`` TAB ``<size>`` TAB ``<path>`` per regular file. Nothing is
#: written anywhere, so the old container's files can be hashed whether it runs or is stopped, and a
#: tmpfs the size of the attachments is not needed.
_HASH_MEMBER = (
    'if [ "$TAR_FILETYPE" = f ]; then '
    'printf "%s\\t%s\\t%s\\n" "$(sha256sum | cut -c1-64)" "$TAR_SIZE" "$TAR_FILENAME"; fi'
)
_HASH_STREAM = f"cd /tmp && tar -x -f - --to-command='{_HASH_MEMBER}'"
#: The same lines for the files of a directory, read through a read-only mount.
_HASH_DIRECTORY = (
    "cd /dst && find . -type f -exec sh -c "
    "'for f; do printf \"%s\\t%s\\t%s\\n\" \"$(sha256sum < \"$f\" | cut -c1-64)\" \"$(wc -c < \"$f\")\" "
    "\"${f#./}\"; done' sh {} +"
)
#: Copies without ever replacing a file the volume holds.
_COPY_STREAM = "tar -x --skip-old-files -f - -C /dst"


def _parse_hashes(output: str) -> dict[str, tuple[str, int]]:
    """Path (relative) → (SHA-256, size) from the lines :data:`_HASH_MEMBER` prints."""
    found: dict[str, tuple[str, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or len(parts[0]) != 64 or not parts[1].isdigit():
            raise ChatFilesNotCarried(f"The helper container answered something unexpected: {line[:120]!r}.")
        found[parts[2].removeprefix("./")] = (parts[0], int(parts[1]))
    return found


def _helper(docker: str, image: str, script: str, *, mount: str | None = None, interactive: bool = False) -> list[str]:
    """``docker run`` of the old gateway's own image, running *script* as root with *mount* on ``/dst``.

    The image is on the server already (an air-gapped one pulls nothing) and is Debian with GNU tar and
    sha256sum. Root, to read and write whatever user the files belong to.
    """
    cmd = [docker, "run", "--rm"]
    if interactive:
        cmd.append("-i")
    cmd += ["--user", "0:0", "--entrypoint", "sh"]
    if mount is not None:
        cmd += ["-v", mount]
    return [*cmd, image, "-c", script]


_NOTHING_LOST = (
    "Nothing was replaced: the old containers still run and the files are untouched in the old gateway "
    "container. Free disk space or fix the cause named, then run the command again."
)


def _stream_from_container(docker: str, image: str, script: str, mount: str | None = None) -> str | None:
    """``docker cp`` of the chat files into a helper container running *script*; its output.

    The tar stream goes from one container to the other, nothing lands on the host, ownership is kept. Works
    on a stopped container too. ``None`` when the old container has no chat files directory; any other
    failure of the copy or the helper raises :class:`ChatFilesNotCarried`.
    """
    reader = subprocess.Popen(  # noqa: S603
        [docker, "cp", f"{_GATEWAY_CONTAINER}:{_CHAT_FILES_PATH}/.", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    helper = _helper(docker, image, script, mount=mount, interactive=True)
    try:
        writer = subprocess.run(  # noqa: S603
            helper, stdin=reader.stdout, capture_output=True, check=False
        )
    finally:
        if reader.stdout is not None:
            reader.stdout.close()
    copy_error = reader.stderr.read().decode("utf-8", errors="replace").strip() if reader.stderr else ""
    if reader.wait() != 0:
        if any(text in copy_error.lower() for text in ("could not find the file", "no such file or directory")):
            return None
        raise ChatFilesNotCarried(
            f"Chat attachments could not be read from the old gateway container ({_GATEWAY_CONTAINER}): "
            f"{copy_error[:300] or 'docker cp failed'}. {_NOTHING_LOST}"
        )
    if writer.returncode != 0:
        detail = writer.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise ChatFilesNotCarried(
            f"Chat attachments could not be copied ({detail or 'the helper container failed'}). {_NOTHING_LOST}"
        )
    return writer.stdout.decode("utf-8", errors="replace")


def _hash_destination(docker: str, image: str, target: str) -> dict[str, tuple[str, int]]:
    """Path → (SHA-256, size) of every file in *target* (volume or host directory), read-only."""
    proc = subprocess.run(  # noqa: S603
        _helper(docker, image, _HASH_DIRECTORY, mount=f"{target}:/dst:ro"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise ChatFilesNotCarried(
            f"The files in the data volume could not be read to check them "
            f"({detail or 'the helper failed'}). {_NOTHING_LOST}"
        )
    return _parse_hashes(proc.stdout.decode("utf-8", errors="replace"))


def _named(paths: list[str]) -> str:
    """The first few of *paths* for a message."""
    shown = ", ".join(sorted(paths)[:5])
    return f"{shown} and {len(paths) - 5} more" if len(paths) > 5 else shown


def _check_destination(
    source: dict[str, tuple[str, int]], found: dict[str, tuple[str, int]], *, after_copy: bool
) -> list[str]:
    """The source files missing in *found* (after the copy that is a failure, raised); a file in both with
    another hash is a conflict and raises always -- nothing is ever overwritten."""
    differing = [path for path, (digest, _) in source.items() if path in found and found[path][0] != digest]
    if differing and after_copy:
        raise ChatFilesNotCarried(
            f"{len(differing)} chat attachment(s) differ from the old gateway container after copying: "
            f"{_named(differing)}. They changed in the old container while it was copied, or were damaged "
            f"on the way. {_NOTHING_LOST}"
        )
    if differing:
        raise ChatFilesNotCarried(
            f"{len(differing)} chat attachment(s) exist in the data volume with other content than in the "
            f"old gateway container: {_named(differing)}. Nothing was overwritten and nothing was replaced; "
            "the old containers still run. Compare the two copies, move the one you do not want out of the "
            "volume (or the old container's file aside), then run the command again."
        )
    missing = [path for path in source if path not in found]
    if missing and after_copy:
        raise ChatFilesNotCarried(
            f"{len(missing)} chat attachment(s) are not in the data volume after copying: {_named(missing)}. "
            f"{_NOTHING_LOST}"
        )
    return missing


def carry_over_chat_files(data_dir: str = "") -> CarryResult:
    """Copy chat attachments an older release left inside the gateway container into the new volume.

    Before that release, ``/data/faden/chat-files`` had no volume: the files lived in the container layer
    and went when the container was replaced. Run before the upgrade replaces it, so a failure of any kind
    stops the command while nothing is replaced: it raises :class:`ChatFilesNotCarried`.

    The files are hashed (SHA-256) in the old container, copied, and hashed again in the volume (or the
    directory under *data_dir*), mounted read-only; every file must be there with the same hash. A volume that
    holds files is no obstacle -- the copy skips files that exist (``tar --skip-old-files``) and a file that
    exists with other content is a conflict that stops everything before anything is written. Returns
    ``"copied"``, ``"present"`` (the old container already has the mount) or ``"none"`` (no old container, or
    it has no chat files).

    The source is hashed by streaming ``docker cp`` into a helper container that hashes each tar member as it
    passes (GNU tar ``--to-command``): nothing is extracted, so no disk or tmpfs of the attachments' size
    is needed, and ``docker cp`` reads a stopped container as well as a running one.
    """
    docker = _docker_bin()
    proc = subprocess.run(  # noqa: S603
        [docker, "inspect", "-f", "{{.Image}} {{range .Mounts}}{{.Destination}} {{end}}", _GATEWAY_CONTAINER],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        if "no such" in stderr.lower():
            return CarryResult("none")  # a fresh install: no old container
        raise ChatFilesNotCarried(
            f"Docker could not inspect {_GATEWAY_CONTAINER}: {stderr.strip()[:300]}. {_NOTHING_LOST}"
        )
    image, *mounts = proc.stdout.decode("utf-8", errors="replace").split()
    if _CHAT_FILES_PATH in mounts:
        return CarryResult("present")

    info("Reading the chat attachments in the old gateway container…")
    listing = _stream_from_container(docker, image, _HASH_STREAM)
    if listing is None:
        return CarryResult("none")  # the old container has no such directory: nothing to carry
    source = _parse_hashes(listing)
    if not source:
        return CarryResult("none")
    size = sum(n for _, n in source.values())
    info(f"  {len(source)} file(s), {_human_size(size)}; copying into the data volume…")

    project = _compose_project(docker) or _DEFAULT_PROJECT
    target = _volume_source(docker, project, "faden_chat_files", data_dir)
    if target is None:
        # Not made yet: made the way compose makes it, so compose adopts it without a warning.
        target = f"{project}_faden_chat_files"
        made = subprocess.run(  # noqa: S603
            [
                docker, "volume", "create",
                "--label", f"com.docker.compose.project={project}",
                "--label", "com.docker.compose.volume=faden_chat_files",
                target,
            ],
            capture_output=True,
            check=False,
        )
        if made.returncode != 0:
            detail = made.stderr.decode("utf-8", errors="replace").strip()[:300]
            raise ChatFilesNotCarried(
                f"The chat files volume could not be created ({detail}). {_NOTHING_LOST}"
            )

    # A conflict is found before anything is written; what the volume lacks is copied.
    if _check_destination(source, _hash_destination(docker, image, target), after_copy=False):
        if _stream_from_container(docker, image, _COPY_STREAM, mount=f"{target}:/dst") is None:
            raise ChatFilesNotCarried(
                f"The chat attachments vanished from the old gateway container while they were copied. "
                f"{_NOTHING_LOST}"
            )
        _check_destination(source, _hash_destination(docker, image, target), after_copy=True)
    return CarryResult("copied", len(source), size)


def snapshot_volumes(backup_dir: Path, *, volumes: list[str] | None = None, data_dir: str = "") -> list[str]:
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
        source = _volume_source(docker, project, vol, data_dir)
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


def restore_volumes(backup_dir: Path, manifest: dict, *, data_dir: str = "") -> list[str]:
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
        target = _volume_source(docker, project, vol, data_dir)
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
    kind: str = DEFAULT_KIND,
) -> Path:
    """Write ``manifest.json`` with file checksums and metadata.

    *kind* says what made the backup (see :data:`BACKUP_KINDS`), so retention only counts its own kind.
    """
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
        "kind": kind,
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


def _kind_of(backup_dir: Path) -> str | None:
    """What made the backup in *backup_dir*, or ``None`` when its manifest cannot be read.

    A manifest without a kind is an older install's backup, and counts as manual.
    """
    try:
        manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    kind = manifest.get("kind") if isinstance(manifest, dict) else None
    return kind if isinstance(kind, str) and kind else DEFAULT_KIND


def rotate_backups(backup_root: Path, *, retain: int = 5, kind: str = DEFAULT_KIND) -> int:
    """Remove the oldest backups *of this kind* beyond the retention count.

    The nightly, manual and upgrade backups share one directory; each kind keeps its own N, so a manual
    ``--retain 3`` does not cut the nightly set and an upgrade does not push nightly ones out. A backup
    whose manifest cannot be read is left alone.

    Assumes backup directories are named with ISO timestamps so
    lexicographic sort == chronological sort.  Returns the number
    of directories removed.
    """
    if retain <= 0:
        return 0

    dirs = sorted(
        [
            d
            for d in backup_root.iterdir()
            if d.is_dir() and (d / "manifest.json").exists() and _kind_of(d) == kind
        ],
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
    kind: str = DEFAULT_KIND,
) -> BackupResult:
    """Full backup orchestration.

    1. Ensure postgres is running
    2. Create timestamped directory
    3. Dump databases
    4. Snapshot .env
    5. Optionally snapshot volumes
    6. Collect migration heads
    7. Write manifest
    8. Rotate old backups of the same *kind*
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
        result.volumes = snapshot_volumes(backup_dir, data_dir=data_dir_of(ctx.env_file))

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
        kind=kind,
    )

    # 7. Rotate
    removed = rotate_backups(output_dir, retain=retain, kind=kind)
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
        result.volumes_restored = restore_volumes(backup_dir, manifest, data_dir=data_dir_of(ctx.env_file))

    # 5. The certificate material, so the machines still trust this server
    if not db_only and manifest.get("tls_material") and ctx.project_dir:
        console.print("\n[bold cyan]Restoring the certificate material…[/bold cyan]")
        result.tls_restored = restore_tls(
            backup_dir, Path(str(ctx.project_dir)), list(manifest["tls_material"])
        )

    return result
