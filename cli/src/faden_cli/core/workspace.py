"""Shared workspace helpers for ``faden dev`` commands.

``dev init`` clones the Fadenstack repos as a *monorepo* at
``<workspace>/fadenstack/<local_name>``.  Consumers (``dev up``,
``dev status``, …) must resolve service paths the same way, which is
why the helpers live here instead of being duplicated per command.
"""

from __future__ import annotations

import re
from pathlib import Path

CORE_DIRNAME = "fadenstack"

# Well-known Fadenstack dev processes and the log files they write to in
# headless (no terminal-emulator) mode.
LOG_LABELS = {
    "backend": "faden-backend.log",
    "worker": "faden-taskiq-worker.log",
    "frontend": "faden-frontend.log",
    "gateway": "faden-gateway.log",
}


def find_service_dir(workspace: Path, name: str) -> Path:
    """Return the path to a service directory, checking the monorepo first.

    ``faden dev init`` clones everything under
    ``<workspace>/fadenstack/<name>``.  Legacy/flat layouts that keep
    the service directly in ``<workspace>/<name>`` still work.
    """
    monorepo = workspace / CORE_DIRNAME / name
    if monorepo.is_dir():
        return monorepo
    return workspace / name


#: A directory counts as a workspace when these services resolve beneath it,
#: in either layout ``find_service_dir`` understands.  Backend + shared are
#: enough to tell a real checkout from a directory that merely has a similar
#: name: the first is the service ``dev up`` launches, the second holds the
#: compose file and the ``.env`` everything else is derived from.
WORKSPACE_MARKERS = ("backend", "shared")


def is_workspace(path: Path) -> bool:
    """True when *path* already holds a Fadenstack checkout."""
    return all(find_service_dir(path, name).is_dir() for name in WORKSPACE_MARKERS)


def detect_workspace(start: Path | None = None) -> Path | None:
    """Find the nearest workspace root at or above *start* (default: cwd).

    This is what lets a developer who cloned the code themselves skip
    ``dev init``'s clone step: the commands find the checkout instead of
    requiring config written by a previous ``dev init``.

    Searching upwards matters because the directory a developer is standing
    in is usually a service, not the workspace root.  The *nearest* match
    wins, so a nested monorepo checkout resolves to itself rather than to
    some unrelated ancestor.  Returns ``None`` when nothing above *start*
    looks like a checkout, so callers keep their previous behaviour.
    """
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if is_workspace(candidate):
            return candidate
    return None


def resolve_workspace(*, remember: bool = False) -> Path:
    """The workspace the ``dev`` commands should act on.

    One implementation for ``up`` / ``down`` / ``status``, which previously
    each resolved this themselves and would drift apart the moment one of
    them learned something the others did not.

    Order: the configured workspace when it holds a checkout, then detection
    from the cwd, then the configured path (or cwd) unchanged so a caller
    still fails the way it used to rather than in some new way.

    ``remember`` persists a detected workspace to config, so the commands
    that only read it agree with the one that found it.
    """
    from faden_cli.core.console import info, warning
    from faden_cli.core.settings import load_config

    cfg = load_config()
    configured = Path(cfg.dev.workspace_dir) if cfg.dev and cfg.dev.workspace_dir else None
    if configured and is_workspace(configured):
        return configured

    detected = detect_workspace()
    if detected:
        if configured:
            warning(f"Configured workspace {configured} holds no checkout — using {detected}")
        else:
            info(f"Detected workspace at {detected}")
        if remember:
            _remember_workspace(detected)
        return detected

    return configured or Path.cwd()


def _remember_workspace(workspace: Path) -> None:
    """Persist a detected workspace so the read-only dev commands agree."""
    from faden_cli.core.console import warning
    from faden_cli.core.settings import DevConfig, load_config, save_config

    cfg = load_config()
    if cfg.dev and cfg.dev.workspace_dir == str(workspace):
        return
    cfg.dev = cfg.dev or DevConfig()
    cfg.dev.workspace_dir = str(workspace)
    try:
        save_config(cfg)
    except OSError as exc:  # noqa: BLE001 — a read-only config must not stop a dev run
        warning(f"Could not save the detected workspace to config: {exc}")


def resolve_shared_compose(workspace: Path) -> Path | None:
    """Locate the shared docker-compose file for a dev workspace."""
    for candidate in (
        workspace / "shared" / "docker-compose.yaml",
        workspace / "shared" / "docker-compose.yml",
        workspace / CORE_DIRNAME / "shared" / "docker-compose.yaml",
        workspace / CORE_DIRNAME / "shared" / "docker-compose.yml",
        workspace / "infra" / "shared" / "docker-compose.yaml",
        workspace / "infra" / "shared" / "docker-compose.yml",
    ):
        if candidate.exists():
            return candidate
    return None


def log_filename(label: str) -> str:
    """Log file name for a dev process label (``backend``/``worker``/...).

    Unmapped labels are slugified rather than interpolated raw. A label with a
    space fell through this fallback and produced ``faden-api gateway.log`` -- a
    space in a path every caller then has to quote, and which nothing in the
    tooling did.
    """
    known = LOG_LABELS.get(label)
    if known is not None:
        return known
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-").lower() or "process"
    return f"faden-{slug}.log"


def dev_logs_dir() -> Path:
    """Directory holding background (headless) dev process log files."""
    return Path.home() / ".faden-dev" / "logs"
