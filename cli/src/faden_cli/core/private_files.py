"""Files only the user who runs ``faden`` may read: ``.env``, backups, saved credentials.

``.env`` holds every password and key of an install, and a backup holds them and every database. Written
with the default umask they were readable by every local user: ``.env`` 0644 (0664 on a live install),
backups 0775 directories with 0664 dumps, manifest and ``.env.bak``, and root-owned 0644 volume archives.

Compose reads ``.env`` as the user who runs ``faden``, and no container mounts ``.env`` or the backups:
owner-only takes nothing from the services. Windows has no such bits; there these helpers write the files and
leave their access to the folder they are in.
"""

from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path
from typing import BinaryIO

#: Read and written by the owner only.
PRIVATE_FILE = 0o600
#: Entered and listed by the owner only.
PRIVATE_DIR = 0o700


def _posix() -> bool:
    return os.name != "nt"


def open_private(path: Path, *, mode: int = PRIVATE_FILE) -> BinaryIO:
    """Open *path* for writing (truncated), with *mode* set before a byte is written.

    ``open()`` applies a mode to a file it creates only; a file that exists keeps its own, so the mode is set
    on the open file as well. A file another user owns but this one may write (a group-writable ``.env``)
    is still written; its mode stays, and ``faden deploy`` and ``faden upgrade`` say so.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, mode)
    if _posix():
        try:
            os.fchmod(fd, mode)
        except PermissionError:
            pass
        except BaseException:
            os.close(fd)
            raise
    return os.fdopen(fd, "wb")


def write_private(path: Path, data: bytes, *, mode: int = PRIVATE_FILE) -> Path:
    """Write *data* to *path*, readable by its owner only (or with *mode*) from the start."""
    with open_private(path, mode=mode) as handle:
        handle.write(data)
    return path


def make_private(path: Path, *, mode: int = PRIVATE_FILE) -> bool:
    """Give an existing file *mode*; ``False`` when it could not be changed (another user's file)."""
    if not _posix() or not path.is_file() or path.is_symlink():
        return True
    try:
        if stat.S_IMODE(path.stat().st_mode) != mode:
            path.chmod(mode)
    except OSError:
        return False
    return True


def private_dir(path: Path) -> Path:
    """Make *path* (and its parents) and give it :data:`PRIVATE_DIR`; the parents keep their modes."""
    path.mkdir(parents=True, exist_ok=True)
    if _posix():
        # A directory someone else owns, such as a mount point, keeps its mode: its files are still private.
        with contextlib.suppress(OSError):
            path.chmod(PRIVATE_DIR)
    return path


def tighten_tree(root: Path) -> list[Path]:
    """Make *root* and everything below it owner-only: directories 0700, files 0600.

    For an install's backups, from before they were written that way. Returns what could not be changed,
    such as a volume archive a helper container wrote as root; inside a 0700 directory nobody else reaches
    it anyway. Symbolic links are left alone, and not followed.
    """
    if not _posix() or not root.is_dir() or root.is_symlink():
        return []
    stuck: list[Path] = []
    # Top-down and without following links: each directory is listed, then made private.
    for current, _dirs, files in os.walk(root):
        here = Path(current)
        try:
            if stat.S_IMODE(here.stat().st_mode) != PRIVATE_DIR:
                here.chmod(PRIVATE_DIR)
        except OSError:
            stuck.append(here)
        stuck.extend(here / name for name in files if not make_private(here / name))
    return stuck


def secure_install(install_dir: Path) -> list[Path]:
    """Make an install's secrets owner-only: ``.env``, the saved credentials, the backups.

    ``faden deploy`` and ``faden upgrade`` run it, so an install made before keeps nothing readable by
    others. Warns about what could not be changed, and returns it. Files another user owns inside a
    backups directory that is now private (volume archives older backups had written as root) are out of
    everyone else's reach, and only mentioned.
    """
    from faden_cli.core.console import info, warning  # noqa: PLC0415

    install_dir = Path(install_dir)
    exposed = [
        path
        for path in (install_dir / ".env", install_dir / ".bootstrap-credentials")
        if not make_private(path)
    ]
    backups = install_dir / "backups"
    stuck = tighten_tree(backups)
    shielded: list[Path] = []
    (exposed if backups in stuck else shielded).extend(stuck)
    if exposed:
        paths = " ".join(str(path) for path in exposed[:5])
        more = f" (and {len(exposed) - 5} more)" if len(exposed) > 5 else ""  # noqa: PLR2004
        warning(
            "Another user owns these, so they could not be made readable by their owner only:"
            f' {paths}{more}.\n  Fix with: sudo chown -R "$(id -un)" {paths}, then run this command again.'
        )
    if shielded:
        info(
            f"{len(shielded)} file(s) in the backups belong to another user (archives older backups wrote as"
            " root); the backups directory is private, so nobody else reaches them."
        )
    return exposed
