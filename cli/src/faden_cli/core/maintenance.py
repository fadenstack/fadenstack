"""Maintenance mode: the front door answers with a maintenance page.

While ``nginx/maintenance.flag`` exists in the install, nginx answers every
page with ``nginx/maintenance.html`` -- which reloads itself once the
platform is back -- and the APIs with a 503 saying so (see
``shared/nginx/locations.conf``). Only ``/api/health`` and nginx's own
health check stay open. The flag is plain JSON the page reads to say why:
``{"reason": "upgrade" | "manual", "since": ..., "message": ...}``.

``faden upgrade`` puts it down for its restart and lifts it once the backend
is healthy again; ``faden maintenance on|off`` does it by hand.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FLAG = Path("nginx") / "maintenance.flag"


def flag_path(install_dir: Path) -> Path:
    """Where the flag lives in *install_dir* (nginx reads /etc/nginx/faden/maintenance.flag)."""
    return install_dir / FLAG


def state(install_dir: Path) -> dict[str, Any] | None:
    """What the flag says, or ``None`` when the platform is not in maintenance."""
    path = flag_path(install_dir)
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        found = {}
    return found if isinstance(found, dict) else {}


def enter(install_dir: Path, *, reason: str, message: str | None = None) -> Path:
    """Put the flag down: from now on nginx answers with the maintenance page."""
    path = flag_path(install_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {"reason": reason, "since": datetime.now(tz=UTC).isoformat(timespec="seconds")}
    if message:
        doc["message"] = message
    # Readable by nginx's worker user whatever the umask: the install's nginx
    # directory is bind-mounted read-only into the container.
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    path.chmod(0o644)
    return path


def leave(install_dir: Path) -> bool:
    """Lift the flag; whether there was one."""
    path = flag_path(install_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


@contextmanager
def during(install_dir: Path, *, reason: str) -> Iterator[None]:
    """Maintenance for the block, lifted however it ends.

    A failed upgrade must not leave the platform answering "maintenance":
    whatever runs then is better reached than hidden. An operator who had
    switched maintenance on by hand keeps it on.
    """
    if state(install_dir) is not None:
        yield
        return
    enter(install_dir, reason=reason)
    try:
        yield
    finally:
        leave(install_dir)
