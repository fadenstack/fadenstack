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
from typing import TYPE_CHECKING, BinaryIO

from faden_cli import __version__
from faden_cli.core import backup_crypto, restore_swap
from faden_cli.core.compose import ComposeContext, up as compose_up
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.private_files import PRIVATE_DIR, open_private, private_dir, write_private
from faden_cli.core.registry import DATABASES, POSTGRES_CONTAINER

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

# Named Docker volumes that can be included in a backup, by their name in the compose file. Not among them:
# Postgres's data volume, because the dumps are the databases' backup, a copy of a running data directory is
# not consistent, and writing one back under the Postgres a restore runs would break it; and the models,
# which can be downloaded again and run to hundreds of gigabytes.
BACKUP_VOLUMES: list[str] = [
    "faden_audit",
    "faden_rag_lite",
    "faden_chat_files",
    "minio_data",
    "ch_data",
    "ch_logs",
    "loki_data",
    "grafana_data",
]

#: Volumes in every backup, with or without ``--include-volumes``.
ALWAYS_BACKED_UP: tuple[str, ...] = ("faden_audit",)

#: The company data volumes docker-compose.yaml can put in a host directory instead: with FADEN_DATA_DIR set in
#: .env, the volume is that directory plus the subdirectory named here. Mirrors the mounts in the compose file.
#: This is the whole scope, settled before the first release that ships it: chat attachments and the knowledge
#: base's files. Never in it, so that setting FADEN_DATA_DIR can never put a volume on an empty folder in a
#: later release: the database (pg_data), the model store (faden_models) and the observability data. They get
#: settings of their own. Adding an entry here later would make every install that set FADEN_DATA_DIR mount an
#: empty folder, and its data would look lost.
DATA_DIR_VOLUMES: dict[str, str] = {"faden_chat_files": "chat-files", "faden_rag_lite": "knowledge"}

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
    "faden-provenance-migrator": "faden_provenance",
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
    #: The id of the backup key it is encrypted to; empty when it is not encrypted.
    encrypted_to: str = ""
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


def dump_files(backup_dir: Path, manifest: dict) -> dict[str, Path]:
    """Each database of the backup and its dump file, in the manifest's order.

    Every one is restored, whatever its migration head (an empty one: a module's database, ``rag``,
    ``langfuse``). A dump the manifest names that is not there refuses the restore before anything changes.
    """
    found: dict[str, Path] = {}
    for db, meta in (manifest.get("databases") or {}).items():
        name = str((meta or {}).get("file") or f"{db}.dump")
        # A file of the backup folder itself, never a path out of it (ASVS 5.0 5.3.2).
        if Path(name).name != name or name in {".", ".."} or "\\" in name:
            raise restore_swap.RestoreRefused(
                f"The manifest names {name!r} as the dump of {db}: not a file of the backup. Nothing was changed."
            )
        path = backup_dir / name
        if not path.is_file():
            raise restore_swap.RestoreRefused(f"The dump of {db} is missing from the backup ({path.name}). Nothing was changed.")
        found[db] = path
    return found


# ── Migration heads ───────────────────────────────────────────────


def get_migration_heads(ctx: ComposeContext) -> dict[str, str]:
    """Collect current Alembic revision heads from migrator containers.

    Runs ``alembic current`` inside each migrator service.  Returns a
    mapping of *database name* → *revision hash* (or empty string if
    the head could not be determined).

    Only the migrators of the services this install runs: a module that is switched off has nothing to read,
    and its image need not be on the server. ``--pull never``: a backup never downloads an image (0.4.5 pulled
    the switched-off mcp and skills images, 1.9 GB, and a server without registry access would wait on them).
    """
    docker = _docker_bin()
    heads: dict[str, str] = {}
    listed = subprocess.run(  # noqa: S603
        [docker, "compose", *_compose_flags(ctx), "config", "--services"],
        capture_output=True,
        text=True,
        check=False,
    )
    active = set(listed.stdout.split()) if listed.returncode == 0 else set(_MIGRATOR_SERVICES)

    for service, db in _MIGRATOR_SERVICES.items():
        if service not in active:
            heads[db] = ""
            continue
        proc = subprocess.run(  # noqa: S603
            [
                docker, "compose",
                *_compose_flags(ctx),
                "run", "--rm", "--no-deps", "--pull", "never", service,
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
    """Restore the .env snapshot from a backup, readable by its owner only.

    The provenance hash-key and watermark-key versions the install had stay beside the backup's: what was
    hashed or watermarked with any of them is checked with it (core/provenance_keys.py).
    """
    from faden_cli.core import provenance_keys  # noqa: PLC0415
    from faden_cli.core.env_gen import read_env_file  # noqa: PLC0415

    src = backup_dir / ".env.bak"
    if not src.exists():
        warning("  No .env snapshot in backup.")
        return False
    before = read_env_file(env_path) if env_path.is_file() else {}
    write_private(env_path, src.read_bytes())
    for note in provenance_keys.keep_after_restore(env_path, before):
        warning(f"  {note}")
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


class FilesNotCarried(RuntimeError):
    """Company files could not be copied and verified. The message says what failed and what to do. Raised
    before any container is replaced, so the files are where they were."""


class ChatFilesNotCarried(FilesNotCarried):
    """The chat attachments in the old gateway container could not be copied and verified."""


class DataDirNotMoved(FilesNotCarried):
    """The files of a named volume could not be copied into the FADEN_DATA_DIR folder and verified."""


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


def _parse_hashes(output: str, failure: type[FilesNotCarried] = ChatFilesNotCarried) -> dict[str, tuple[str, int]]:
    """Path (relative) → (SHA-256, size) from the lines :data:`_HASH_MEMBER` prints."""
    found: dict[str, tuple[str, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or len(parts[0]) != 64 or not parts[1].isdigit():
            raise failure(f"The helper container answered something unexpected: {line[:120]!r}.")
        found[parts[2].removeprefix("./")] = (parts[0], int(parts[1]))
    return found


def _helper(
    docker: str,
    image: str,
    script: str,
    *,
    mount: str | None = None,
    interactive: bool = False,
    extra_mounts: tuple[str, ...] = (),
) -> list[str]:
    """``docker run`` of an image already on the server, running *script* as root with *mount* on ``/dst``.

    The image is the old gateway's or the backend's own (an air-gapped server pulls nothing) and is Debian
    with GNU tar and sha256sum. Root, to read and write whatever user the files belong to.
    """
    cmd = [docker, "run", "--rm"]
    if interactive:
        cmd.append("-i")
    cmd += ["--user", "0:0", "--entrypoint", "sh"]
    for each in (*([mount] if mount is not None else []), *extra_mounts):
        cmd += ["-v", each]
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
    if listing is None or not (source := _parse_hashes(listing)):
        # No such directory, or an empty one: said, so the line above does not end in silence.
        info("  No chat attachments there: nothing to carry over.")
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


#: The same lines for the files of a named volume, mounted read-only on /src.
_HASH_VOLUME = _HASH_DIRECTORY.replace("/dst", "/src")
#: Volume -> folder without ever replacing a file the folder holds. No ``pipefail`` in dash: a failed read side
#: shows up as files missing in the check after the copy.
_MOVE_VOLUME = "tar -c -f - -C /src . | tar -x --skip-old-files -f - -C /dst"

_VOLUME_LEFT = (
    "Nothing was replaced: the containers still run and the named volume is untouched. Fix the cause named, "
    "then run the command again."
)


@dataclass
class MoveResult:
    """What :func:`move_into_data_dir` did for one volume: ``"moved"``, ``"present"`` (the folder already held
    every file) or ``"none"`` (no such volume, or it is empty), and how many files and bytes were copied."""

    volume: str
    status: str
    folder: str = ""
    files: int = 0
    size: int = 0


def _helper_image(docker: str, ctx: object | None = None) -> str:
    """An image already on the server for the helper container: the running gateway's or backend's own,
    else the backend image the compose file names (an air-gapped server pulls nothing)."""
    for container in (_GATEWAY_CONTAINER, "faden-backend"):
        proc = subprocess.run(  # noqa: S603
            [docker, "inspect", "-f", "{{.Image}}", container], capture_output=True, check=False
        )
        image = proc.stdout.decode("utf-8", errors="replace").strip() if proc.returncode == 0 else ""
        if image:
            return image
    if ctx is not None:
        proc = subprocess.run(  # noqa: S603
            [*ctx.base_cmd(), "config", "--images"], capture_output=True, text=True, check=False  # type: ignore[attr-defined]
        )
        for line in (proc.stdout or "").splitlines():
            if proc.returncode == 0 and "/backend:" in line.strip():
                return line.strip()
    raise DataDirNotMoved(
        f"No image to copy the files with: neither {_GATEWAY_CONTAINER} nor faden-backend exists and the compose "
        f"file names no backend image. {_VOLUME_LEFT}"
    )


def _hash_tree(docker: str, image: str, path: str, *, role: str) -> dict[str, tuple[str, int]]:
    """Path → (SHA-256, size) of every file under *path* (a volume, as ``/src``, or a folder, as ``/dst``)."""
    script, where = (_HASH_VOLUME, "src") if role == "volume" else (_HASH_DIRECTORY, "dst")
    proc = subprocess.run(  # noqa: S603
        _helper(docker, image, script, mount=f"{path}:/{where}:ro"), capture_output=True, check=False
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise DataDirNotMoved(
            f"The files in the {role} {path} could not be read to check them "
            f"({detail or 'the helper failed'}). {_VOLUME_LEFT}"
        )
    return _parse_hashes(proc.stdout.decode("utf-8", errors="replace"), DataDirNotMoved)


def _move_volume(docker: str, image: str, name: str, volume: str, folder: str) -> MoveResult:
    """Copy what the named *volume* holds and *folder* lacks into *folder*, then check every file's SHA-256."""
    source = _hash_tree(docker, image, volume, role="volume")
    if not source:
        return MoveResult(name, "none", folder)
    found = _hash_tree(docker, image, folder, role="folder")
    differing = [path for path, (digest, _) in source.items() if path in found and found[path][0] != digest]
    if differing:
        raise DataDirNotMoved(
            f"{len(differing)} file(s) of {name} exist in {folder} with other content than in the volume: "
            f"{_named(differing)}. Nothing was overwritten and nothing was replaced. Compare the two copies, "
            f"move the one you do not want out of the way, then run the command again."
        )
    size = sum(n for _, n in source.values())
    if all(path in found for path in source):
        return MoveResult(name, "present", folder)
    info(f"  {name}: {len(source)} file(s), {_human_size(size)}; copying into {folder}…")
    proc = subprocess.run(  # noqa: S603
        _helper(docker, image, _MOVE_VOLUME, mount=f"{folder}:/dst", extra_mounts=(f"{volume}:/src:ro",)),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise DataDirNotMoved(
            f"The files of {name} could not be copied into {folder} ({detail or 'the helper container failed'}). "
            f"{_VOLUME_LEFT}"
        )
    after = _hash_tree(docker, image, folder, role="folder")
    differing = [p for p, (digest, _) in source.items() if p in after and after[p][0] != digest]
    if differing:
        raise DataDirNotMoved(
            f"{len(differing)} file(s) of {name} differ in {folder} after copying: {_named(differing)}. They "
            f"changed in the volume while it was copied, or were damaged on the way. {_VOLUME_LEFT}"
        )
    missing = [path for path in source if path not in after]
    if missing:
        raise DataDirNotMoved(
            f"{len(missing)} file(s) of {name} are not in {folder} after copying: {_named(missing)}. {_VOLUME_LEFT}"
        )
    return MoveResult(name, "moved", folder, len(source), size)


def move_into_data_dir(data_dir: str, ctx: object | None = None) -> list[MoveResult]:
    """With FADEN_DATA_DIR set: copy the files of every named volume in :data:`DATA_DIR_VOLUMES` that still
    exists into its folder, verified, before a container that mounts the folder is created.

    Switching FADEN_DATA_DIR on would otherwise put an empty folder where the volume's files are. The copy
    never overwrites (``tar --skip-old-files``); then every file of the volume must be in the folder with the
    same SHA-256. A file in both with other content is a conflict that stops everything before anything is
    written. The volume is left as it was. Any failure raises :class:`DataDirNotMoved`; nothing is replaced.
    Safe to run again: a folder that holds everything is left alone. Switching it off again copies nothing back.
    """
    if not data_dir:
        return []
    docker = _docker_bin()
    project = _compose_project(docker) or _DEFAULT_PROJECT
    results: list[MoveResult] = []
    image = ""
    for name, subdirectory in DATA_DIR_VOLUMES.items():
        volume = _compose_volume(docker, project, name)
        folder = f"{data_dir}/{subdirectory}"
        if volume is None:
            results.append(MoveResult(name, "none", folder))
            continue
        image = image or _helper_image(docker, ctx)
        results.append(_move_volume(docker, image, name, volume, folder))
    return results


def keep_company_files(env_file: object, ctx: object | None = None) -> None:
    """Before containers are created with this ``.env``: :func:`keep_chat_files`, then, when FADEN_DATA_DIR is
    set, :func:`move_into_data_dir`.

    The chat files go first: from an old gateway container (0.4.3 and older, no mount) straight into the
    folder, which creates no named volume; the move after it then finds no volume for them, or one whose files
    the folder already holds. The two never write the same file differently: both skip files that exist and
    stop on a conflict. When anything cannot be kept, the command stops here with the reason; nothing is replaced.
    """
    keep_chat_files(env_file)
    try:
        results = move_into_data_dir(data_dir_of(env_file), ctx)
    except FilesNotCarried as exc:
        error(str(exc))
        sys.exit(1)
    for result in results:
        if result.status == "moved":
            success(
                f"{result.volume} copied into {result.folder} and verified ({result.files} file(s), "
                f"{_human_size(result.size)}). The named volume is left as it was: once you are satisfied, "
                f"remove it with `docker volume rm`."
            )


def _volume_image(docker: str, ctx: object | None) -> str:
    """The image a volume backup or restore runs in: one already on the server (``_helper_image``), so an
    air-gapped server pulls nothing; ``alpine`` only where no Fadenstack image is there at all."""
    try:
        return _helper_image(docker, ctx)
    except DataDirNotMoved:
        return "alpine"


def snapshot_volumes(
    backup_dir: Path, *, volumes: list[str] | None = None, data_dir: str = "", ctx: object | None = None
) -> list[str]:
    """Tar each named Docker volume into the backup directory.

    Uses a temporary container of an image already on the server (``_volume_image``) to read from the
    volume mount. The archive carries the compose
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
    image = ""

    for vol in targets:
        source = _volume_source(docker, project, vol, data_dir)
        if source is None:
            warning(f"  No volume {vol} in this install; skipped")
            continue
        archive = f"{vol}.tar.gz"
        archive_path = backup_dir / archive
        image = image or _volume_image(docker, ctx)
        info(f"  Snapshotting volume: {vol}")
        with open_private(archive_path) as out:
            proc = subprocess.run(  # noqa: S603
                _helper(docker, image, "tar czf - -C /data .", mount=f"{source}:/data:ro"),
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


def restore_volumes(
    backup_dir: Path,
    manifest: dict,
    *,
    data_dir: str = "",
    ctx: object | None = None,
    failed: list[str] | None = None,
) -> list[str]:
    """Restore volume archives from a backup.

    Returns list of volume names that were restored. A volume that could not be restored (its archive missing
    or changed, the copy failing) is warned about and, when *failed* is given, added to it; one the install has
    no volume for is only warned about.
    """
    failures = failed if failed is not None else []
    docker = _docker_bin()
    project = _compose_project(docker)
    archives = manifest.get("volumes", [])
    restored: list[str] = []
    image = ""

    for archive in archives:
        archive_path = backup_dir / archive
        if not archive_path.exists():
            warning(f"  Volume archive missing: {archive}")
            failures.append(archive.removesuffix(".tar.gz"))
            continue
        # Checked here as well as when the backup is verified: the volume is emptied before the archive is
        # written in, so an archive that changed since must not get that far.
        recorded = (manifest.get("volume_checksums") or {}).get(archive)
        if recorded:
            problem = _volume_archive_error(backup_dir, archive, recorded)
            if problem:
                warning(f"  {problem}; {archive} not restored")
                failures.append(archive.removesuffix(".tar.gz"))
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
        image = image or _volume_image(docker, ctx)
        info(f"  Restoring volume: {vol}")
        # The archive goes in on the container's input: the backup is its owner's only, which a container's
        # root may not be (user namespaces), and a mount of it is not needed.
        with archive_path.open("rb") as archive_in:
            proc = subprocess.run(  # noqa: S603
                _helper(
                    docker, image, "find /data -mindepth 1 -delete && tar xzf - -C /data",
                    mount=f"{target}:/data", interactive=True,
                ),
                stdin=archive_in,
                capture_output=True,
                check=False,
            )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            warning(f"  Volume restore failed for {vol}: {stderr[:200]}")
            failures.append(vol)
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


def install_release(env_file: object) -> str:
    """The release the install runs: ``VERSION`` in its ``.env``, or ``""`` when it names none."""
    if not env_file or not Path(str(env_file)).is_file():
        return ""
    from faden_cli.core.env_gen import read_env_file  # noqa: PLC0415

    return read_env_file(Path(str(env_file))).get("VERSION", "").strip()


def describe_release(manifest: dict) -> str:
    """Which release a backup holds, as a restore says it: ``release`` beside ``faden_version``.

    An upgrade takes its backup with the new CLI, before anything changes: ``faden_version`` is then the
    release being installed, the data are the one before. A manifest from before 0.7.0 records only the CLI.
    """
    taken_by = manifest.get("faden_version") or "unknown"
    release = str(manifest.get("release") or "").strip()
    if release:
        return f"Backup of release {release}, taken by faden {taken_by}"
    note = (
        f"Backup taken by faden {taken_by}; the manifest does not record the install's release "
        "(backups taken before 0.7.0 do not)"
    )
    if manifest.get("kind") == "upgrade":
        note += f": an upgrade's backup holds the release before that upgrade, older than {taken_by}"
    return note


def write_manifest(
    backup_dir: Path,
    *,
    databases: dict[str, str],
    env_snapshot: str,
    volumes: list[str],
    migration_heads: dict[str, str],
    tls_material: list[str] | None = None,
    kind: str = DEFAULT_KIND,
    release: str = "",
) -> Path:
    """Write ``manifest.json`` with file checksums and metadata.

    *kind* says what made the backup (see :data:`BACKUP_KINDS`), so retention only counts its own kind.
    *release* is the release the install ran (``.env`` ``VERSION``), recorded beside the CLI's own version: an
    upgrade's backup is taken by the new CLI and holds the old release's data.
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
        "release": release,
        "kind": kind,
        "databases": db_entries,
        "env_snapshot": env_snapshot,
        "env_sha256": _sha256(env_file) if env_snapshot and (env_file := backup_dir / env_snapshot).is_file() else "",
        "volumes": volumes,
        # By archive name, like the dumps and certificate files: a restore empties a volume before it writes
        # the archive in, so a damaged archive has to be found first.
        "volume_checksums": {
            archive: _sha256(backup_dir / archive) for archive in volumes if (backup_dir / archive).is_file()
        },
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


def _volume_archive_error(backup_dir: Path, archive: str, expected: str) -> str | None:
    """Why the volume archive *archive* does not match its recorded checksum, or ``None`` when it does."""
    path = backup_dir / archive
    if not path.is_file():
        return f"Missing volume archive: {archive}"
    if _sha256(path) != expected:
        return f"Checksum mismatch for {archive}"
    return None


def verify_backup(backup_dir: Path, manifest: dict) -> list[str]:
    """Verify the database dumps, volume archives and certificate files against the manifest's checksums.

    Returns a list of error messages (empty if all OK). A manifest from before the volume archives had
    checksums lists none, and those archives are not checked.
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
    for archive, expected in (manifest.get("volume_checksums") or {}).items():
        error_text = _volume_archive_error(backup_dir, archive, expected)
        if error_text:
            errors.append(error_text)
    env_snapshot, env_expected = manifest.get("env_snapshot") or "", manifest.get("env_sha256") or ""
    if env_snapshot and env_expected:  # a manifest from before .env.bak had a checksum has none
        env_path = backup_dir / env_snapshot
        if not env_path.is_file():
            errors.append(f"Missing .env snapshot: {env_snapshot}")
        elif _sha256(env_path) != env_expected:
            errors.append(f"Checksum mismatch for {env_snapshot}")
    for rel, expected in (manifest.get("tls_material") or {}).items():
        path = backup_dir / _TLS_BACKUP_DIR / rel
        if not path.is_file():
            errors.append(f"Missing certificate file: {rel}")
        elif _sha256(path) != expected:
            errors.append(f"Checksum mismatch for {rel}")
    return errors


# ── Retention ─────────────────────────────────────────────────────


def is_backup(folder: Path) -> bool:
    """Whether *folder* is a backup: its manifest, or the index of an encrypted one."""
    return (folder / "manifest.json").is_file() or backup_crypto.is_encrypted(folder)


def _kind_of(backup_dir: Path) -> str | None:
    """What made the backup in *backup_dir*, or ``None`` when its manifest cannot be read.

    A manifest without a kind is an older install's backup, and counts as manual. An encrypted backup says
    its kind in the clear, in its index.
    """
    if backup_crypto.is_encrypted(backup_dir):
        manifest = backup_crypto.read_index(backup_dir)
        if manifest is None:
            return None
    else:
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
            if d.is_dir() and is_backup(d) and _kind_of(d) == kind
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
    8. Encrypt every file to the install's backup key, when it has one
    9. Rotate old backups of the same *kind*
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

    # 4. Snapshot volumes. The audit trail's file copy always: it is small, and an audit trail kept as long as
    # the system lives has to survive a restore (features/admin-guardrails.md, D-B).
    if include_volumes and not db_only:
        console.print("\n[bold cyan]Snapshotting Docker volumes…[/bold cyan]")
        result.volumes = snapshot_volumes(backup_dir, data_dir=data_dir_of(ctx.env_file), ctx=ctx)
    elif not db_only:
        console.print("\n[bold cyan]Snapshotting the audit trail…[/bold cyan]")
        result.volumes = snapshot_volumes(
            backup_dir, volumes=list(ALWAYS_BACKED_UP), data_dir=data_dir_of(ctx.env_file), ctx=ctx,
        )

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
        release=install_release(ctx.env_file),
    )

    # 7. Encrypt, when the install has a backup key (faden backup key create): the manifest too
    recipient = _recipient(ctx, result)
    if not result.ok:
        return result
    if recipient is not None:
        console.print("\n[bold cyan]Encrypting the backup…[/bold cyan]")
        backup_crypto.encrypt_backup(backup_dir, recipient, kind=kind)
        result.encrypted_to = backup_crypto.key_id(recipient)

    # 8. Rotate
    removed = rotate_backups(output_dir, retain=retain, kind=kind)
    if removed:
        info(f"Rotated {removed} old backup(s).")

    return result


def _recipient(ctx: ComposeContext, result: BackupResult) -> X25519PublicKey | None:
    """The install's backup key; a key file that does not read fails the backup rather than leave it open."""
    from faden_cli.core import backup_key  # noqa: PLC0415

    # The install folder is .env's (the compose file may be in its shared/), as `faden backup key` has it.
    install = Path(str(ctx.env_file)).parent if ctx.env_file else ctx.project_dir
    if install is None:
        return None
    try:
        return backup_key.recipient(Path(str(install)))
    except (OSError, ValueError) as exc:
        result.ok = False
        result.errors.append(
            f"The backup key ({backup_key.PUBLIC_FILE}) does not read: {exc}. The backup was not encrypted; "
            f"remove {result.backup_dir} and fix the key file, or make a new key with "
            "`faden backup key create --replace`."
        )
        return None


# ── Restore: beside the live install, then swapped in ─────────────
#
# The databases are restored beside the live ones while the install runs (core/restore_swap.py), then swapped in
# with the services stopped. What else a restore replaces -- .env, the volumes, the certificate material and
# RabbitMQ's users -- is kept first, so a restore whose services do not come back healthy puts all of it back.


@dataclass
class Prepared:
    """What a restore found and made before anything the install runs on changed."""

    names: list[str]
    #: The server's databases when the restore began, with their sizes.
    live: dict[str, int]
    #: The install's databases the backup does not hold: left as they are.
    not_in_backup: list[str]
    #: The volumes the backup replaces (the ones this install has).
    volumes: list[str]
    #: Copies an earlier, interrupted restore had left, dropped before this one began.
    dropped: list[str] = field(default_factory=list)


def restore_stamp() -> str:
    """The time a restore names its log and its folder of kept files by."""
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def restore_log_path(install: Path, stamp: str) -> Path:
    """``<install>/logs/restore-<time>.log``: pg_restore's whole output, readable by its owner only (it can
    quote rows of the databases; ASVS 5.0 14.2.4, 16.2.5)."""
    return private_dir(install / "logs") / f"restore-{stamp}.log"


def volumes_replaced(manifest: dict, ctx: ComposeContext) -> list[str]:
    """The volumes a restore of *manifest* writes over: those in the backup this install has."""
    docker = _docker_bin()
    project = _compose_project(docker)
    data_dir = data_dir_of(ctx.env_file)
    names: list[str] = []
    for archive in manifest.get("volumes") or []:
        vol = str(archive).removesuffix(".tar.gz")
        if vol not in _NEVER_RESTORED and _volume_source(docker, project, vol, data_dir) is not None:
            names.append(vol)
    return names


def _volume_bytes(volumes: list[str], ctx: ComposeContext) -> int:
    """What *volumes* hold now (``du``): the room their copies before the restore take, at most."""
    if not volumes:
        return 0
    docker = _docker_bin()
    project = _compose_project(docker)
    data_dir = data_dir_of(ctx.env_file)
    image = _volume_image(docker, ctx)
    total = 0
    for vol in volumes:
        source = _volume_source(docker, project, vol, data_dir)
        if source is None:
            continue
        proc = subprocess.run(  # noqa: S603
            _helper(docker, image, "du -sk /data", mount=f"{source}:/data:ro"), capture_output=True, check=False
        )
        first = proc.stdout.decode("utf-8", errors="replace").split()
        if proc.returncode != 0 or not first or not first[0].isdigit():
            raise restore_swap.RestoreRefused(f"How much the volume {vol} holds could not be read. Nothing was changed.")
        total += int(first[0]) * 1024
    return total


def _host_free(path: Path) -> tuple[str, int]:
    """The filesystem *path* is on (as ``df`` names it, where there is a ``df``) and its free bytes."""
    free = shutil.disk_usage(path).free
    df = shutil.which("df")
    if not df:
        return "", free
    proc = subprocess.run([df, "-Pk", str(path)], capture_output=True, check=False)  # noqa: S603
    lines = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
    return (lines[-1].split()[0] if proc.returncode == 0 and len(lines) >= 2 else ""), free


def disk_need(
    dumps: dict[str, Path], live: dict[str, int], volumes: list[str], ctx: ComposeContext, work_parent: Path
) -> restore_swap.DiskNeed:
    """Room for a copy of every database of the backup beside the live ones, and for a copy of the volumes it
    replaces (kept until the restore is healthy); see :mod:`faden_cli.core.restore_swap`."""
    total, detail = restore_swap.database_need({db: path.stat().st_size for db, path in dumps.items()}, live)
    filesystem, free = restore_swap.data_free()
    host_filesystem, host_free = _host_free(work_parent)
    return restore_swap.DiskNeed(
        databases=total,
        volumes=_volume_bytes(volumes, ctx),
        reserve=max(restore_swap.RESERVE_BYTES, total // 10),
        data_free=free,
        data_filesystem=filesystem,
        host_free=host_free,
        host_filesystem=host_filesystem,
        detail=detail,
    )


def prepare_restore(
    ctx: ComposeContext,
    backup_dir: Path,
    manifest: dict,
    log: BinaryIO,
    *,
    db_only: bool,
    work_parent: Path,
) -> Prepared:
    """Everything a restore does before it changes what the install runs on; Postgres must be running.

    The names checked; copies an interrupted restore left (``<db>__restore``) dropped; an old database one left
    (``<db>__old``, the database before that restore) refuses; the room checked; then every database restored
    beside the live one. :class:`~faden_cli.core.restore_swap.RestoreRefused` or ``RestoreFailed`` when it
    cannot be done: then nothing the install runs on was changed, and no copy is left.
    """
    rs = restore_swap
    dumps = dump_files(backup_dir, manifest)
    names = list(dumps)
    if not names:
        raise rs.RestoreRefused("The backup holds no database. Nothing was changed.")
    rs.check_names(names)
    live = rs.databases()
    copies, olds = rs.leftovers(names, live)
    if olds:
        drops = " ".join(f'-c "DROP DATABASE {rs.quote(name)};"' for name in olds)
        raise rs.RestoreRefused(
            f"An earlier restore left {', '.join(olds)}: the database(s) as they were before that restore. "
            "Check them, and drop them once they are not needed: "
            f"docker exec {rs.container()} psql -U postgres {drops}. Nothing was changed."
        )
    if copies:
        left = rs.drop(copies)
        if left:
            raise rs.RestoreRefused(
                f"An earlier restore left {', '.join(copies)}, and they could not be dropped: {'; '.join(left)}. "
                "Nothing was changed."
            )
        info(f"  Dropped what an interrupted restore had left: {', '.join(copies)}")
        live = rs.databases()
    volumes = [] if db_only else volumes_replaced(manifest, ctx)
    need = disk_need(dumps, live, volumes, ctx, work_parent)
    if problems := need.problems():
        raise rs.RestoreRefused(
            "Not enough room for the restore, which keeps a copy of what it replaces until the restored install "
            f"is healthy: {'; '.join(problems)}. Free that much (or restore on a server with more room). "
            "Nothing was changed."
        )
    reserved = rs.human(need.databases + need.reserve + (need.volumes if need.shared else 0))
    info(f"  Room: {rs.human(need.data_free)} free, {reserved} needed.")
    ignore = {"postgres", "template0", "template1"}
    not_in_backup = sorted(
        db for db in live if db not in ignore and db not in names and not db.endswith((rs.RESTORE_SUFFIX, rs.OLD_SUFFIX))
    )
    for db in names:
        info(f"  Restoring {db} into {db}{rs.RESTORE_SUFFIX}")
    rs.restore_beside(dumps, live, log)
    return Prepared(names, live, not_in_backup, volumes, dropped=copies)


class FilesNotKept(RuntimeError):
    """What a restore replaces could not be kept first; nothing of it was replaced yet."""


@dataclass
class Kept:
    """What a restore replaced beside the databases, kept so it can go back: the bytes of the files, ``None``
    for a file the install did not have, and the volumes' archives in *work_dir*."""

    work_dir: Path
    env_path: Path | None = None
    env: bytes | None = None
    rabbitmq_path: Path | None = None
    rabbitmq: bytes | None = None
    rabbitmq_mode: int = 0o644
    install_dir: Path | None = None
    tls: dict[str, bytes | None] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)

    def discard(self) -> None:
        """Remove the kept copies (the restore is healthy, or they went back)."""
        shutil.rmtree(self.work_dir, ignore_errors=True)


#: Where RabbitMQ's users are written from .env (``faden deploy`` and ``faden upgrade`` write it the same way).
RABBITMQ_DEFINITIONS = Path("rabbitmq") / "definitions.json"


def keep_before_restore(
    ctx: ComposeContext,
    manifest: dict,
    work_dir: Path,
    *,
    db_only: bool,
    skip_env: bool,
    volumes: list[str],
) -> Kept:
    """Keep what a restore of *manifest* will replace, before it does; owner-only in *work_dir*.

    ``.env`` and RabbitMQ's users (written from it) when the backup's ``.env`` goes in; the certificate files
    the backup holds; an archive of each volume it replaces, every one of them, or :class:`FilesNotKept`.
    """
    private_dir(work_dir)
    kept = Kept(work_dir=work_dir)
    install = Path(str(ctx.project_dir)) if ctx.project_dir else None
    if not db_only and not skip_env and manifest.get("env_snapshot") and ctx.env_file:
        kept.env_path = Path(str(ctx.env_file))
        kept.env = kept.env_path.read_bytes() if kept.env_path.is_file() else None
        if kept.env is not None:
            write_private(work_dir / ".env.before", kept.env)
        if install is not None:
            kept.rabbitmq_path = install / RABBITMQ_DEFINITIONS
            if kept.rabbitmq_path.is_file():
                kept.rabbitmq = kept.rabbitmq_path.read_bytes()
                kept.rabbitmq_mode = kept.rabbitmq_path.stat().st_mode & 0o777
    if not db_only and manifest.get("tls_material") and install is not None:
        kept.install_dir = install
        for rel in manifest["tls_material"]:
            if rel in TLS_MATERIAL:
                path = install / rel
                kept.tls[rel] = path.read_bytes() if path.is_file() else None
    if volumes:
        made = snapshot_volumes(work_dir, volumes=volumes, data_dir=data_dir_of(ctx.env_file), ctx=ctx)
        missing = sorted(set(volumes) - {archive.removesuffix(".tar.gz") for archive in made})
        if missing:
            raise FilesNotKept(f"The volumes {', '.join(missing)} could not be copied before the restore.")
        kept.volumes = made
    return kept


def put_back(kept: Kept, ctx: ComposeContext) -> list[str]:
    """Put back what :func:`keep_before_restore` kept; what could not go back, one line each (empty: all did)."""
    problems: list[str] = []
    if kept.env_path is not None:
        try:
            if kept.env is None:
                kept.env_path.unlink(missing_ok=True)
            else:
                write_private(kept.env_path, kept.env)
        except OSError as exc:
            problems.append(f".env: {exc} (the install's own is in {kept.work_dir / '.env.before'})")
    if kept.rabbitmq_path is not None:
        try:
            if kept.rabbitmq is None:
                kept.rabbitmq_path.unlink(missing_ok=True)
            else:
                kept.rabbitmq_path.write_bytes(kept.rabbitmq)
                kept.rabbitmq_path.chmod(kept.rabbitmq_mode)
        except OSError as exc:
            problems.append(f"{RABBITMQ_DEFINITIONS}: {exc}")
    for rel, data in kept.tls.items():
        path = (kept.install_dir or Path(".")) / rel
        try:
            if data is None:
                path.unlink(missing_ok=True)
            else:
                write_private(path, data, mode=_tls_mode(rel))
        except OSError as exc:
            problems.append(f"{rel}: {exc}")
    if kept.volumes:
        failed: list[str] = []
        restore_volumes(
            kept.work_dir, {"volumes": kept.volumes}, data_dir=data_dir_of(ctx.env_file), ctx=ctx, failed=failed
        )
        problems.extend(
            f"the volume {vol}: still the backup's (its content before the restore: "
            f"{kept.work_dir / (vol + '.tar.gz')})"
            for vol in failed
        )
    return problems
