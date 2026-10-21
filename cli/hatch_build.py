"""Pack ``shared`` into the CLI, so ``pip install fadenstack`` can deploy.

A deployment needs the compose file and what it mounts (nginx, Prometheus,
Grafana, ClickHouse, Loki, RabbitMQ, initdb). Those live in
``../shared``; a user who installed only the CLI has no checkout, and
``faden deploy`` used to stop at "Cannot find shared".

The wheel carries them at ``faden_cli/bundle/shared``. An sdist carries them at
``src/faden_cli/bundle/shared``, so a wheel built from the sdist -- where
``../shared`` does not exist -- picks them up as package files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:  # the tests load this file for bundle_files() alone
    BuildHookInterface = object  # type: ignore[assignment,misc]

#: What an install writes or generates for itself, and what is only for
#: building from source. None of it belongs in a release.
_SKIP_DIRS = {"backups", "base", ".empty-hf-cache", "__pycache__", "secrets"}
_SKIP_FILES = {
    ".env",
    ".bootstrap-credentials",
    ".gitattributes",
    # Written by deploy from .env, with the snippet that loads them; not tracked.
    "rabbitmq/definitions.json",
    "rabbitmq/10-faden.conf",
    # Written by the backend at run time; an install starts it as "[]".
    "prometheus/targets.json",
    # Development-only overlay (mounts source trees that a release lacks).
    "docker-compose.dev.yaml",
}


def _tracked(shared: Path) -> set[str] | None:
    """The files of *shared* git tracks, or ``None`` outside a git checkout.

    A working checkout also holds what the running backend generated (its
    Grafana dashboards, per runtime); only what is committed is a release.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "."],  # noqa: S607 - git from PATH, as any build
            cwd=shared, capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {name for name in out.decode().split("\x00") if name}


def bundle_files(shared: Path) -> list[tuple[Path, str]]:
    """Every file of *shared* a release ships, with its path inside the bundle."""
    tracked = _tracked(shared)
    found: list[tuple[Path, str]] = []
    for path in sorted(shared.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(shared).as_posix()
        if tracked is not None and rel not in tracked:
            continue
        parts = rel.split("/")
        if parts[0] in _SKIP_DIRS or rel in _SKIP_FILES or rel.startswith(".env"):
            continue
        # The binaries directory ships empty: builds are placed there per install.
        if parts[0] == "agent-binaries" and rel != "agent-binaries/.gitignore":
            continue
        found.append((path, rel))
    return found


class CustomBuildHook(BuildHookInterface):
    """Add the shared deployment files to the wheel or sdist being built."""

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        catalog = Path(self.root).parent / "pii/faden_pii/pii-catalog.json"
        shared = Path(self.root).parent / "shared"
        if catalog.is_file():
            prefix = "src/faden_cli" if self.target_name == "sdist" else "faden_cli"
            build_data["force_include"][str(catalog)] = f"{prefix}/bundle/pii-catalog.json"
        elif shared.is_dir():
            # From a checkout that lacks pii/: the CLI would not start (deploy reads the catalog on import).
            msg = f"The PII catalog {catalog} is missing; the CLI cannot be built without it."
            raise FileNotFoundError(msg)
        if not shared.is_dir():
            # Building from an sdist: the files are already in the package.
            return
        prefix = "src/faden_cli/bundle/shared" if self.target_name == "sdist" else "faden_cli/bundle/shared"
        for path, rel in bundle_files(shared):
            build_data["force_include"][str(path)] = f"{prefix}/{rel}"
