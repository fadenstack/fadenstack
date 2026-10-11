"""What a Fadenstack installation runs: its release, or the code its images were built from.

An install made from the published images carries the release it was unpacked from (``.faden-release``,
:mod:`faden_cli.core.bundle`); its images are that release's. A source checkout builds its images from
whatever the checkout holds at the time, and the checkout can move on afterwards -- so ``faden upgrade`` and
``faden deploy`` record, when they build, which commit the images came from (:data:`BUILD_RECORD`).
``faden version`` reads both, so an operator can always say what the server runs.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from faden_cli import __version__
from faden_cli.core.bundle import installed_release

#: Written into the install directory when its images are built from a checkout.
BUILD_RECORD = ".faden-build"

#: Tracked files the running install rewrites itself: a change to them is not a local change to the code.
GENERATED = frozenset({
    "shared/prometheus/targets.json", "shared/rabbitmq/definitions.json", "shared/rabbitmq/10-faden.conf",
})

_AGENT_VERSION = re.compile(r'^AGENT_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)


def checkout_of(install_dir: Path) -> Path | None:
    """The source checkout an install directory belongs to (``<checkout>/shared``), or ``None``."""
    for candidate in (install_dir, *install_dir.parents[:2]):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def checkout_state(repo: Path) -> dict[str, str | bool | None]:
    """The checkout's commit, a readable name for it, and whether it has local changes.

    The name is the release the commit follows (``v0.4.1-3-gabc1234``), or the commit when no release tag is
    reachable; the repository's other tags (the agent's, older ones) say nothing about the server.
    """
    changed = (_git(repo, "diff", "--name-only", "HEAD") or "").splitlines()
    commit = _git(repo, "rev-parse", "--short=8", "HEAD")
    return {
        "commit": commit,
        "describe": _git(repo, "describe", "--tags", "--match", "v[0-9]*") or commit,
        "branch": _git(repo, "symbolic-ref", "--short", "-q", "HEAD"),
        "modified": any(path not in GENERATED for path in changed),
    }


def record_build(install_dir: Path, *, complete: bool = True) -> dict | None:
    """Note which commit this install's images were just built from; ``None`` outside a checkout.

    ``complete`` is false when some images failed to build: those still run the code of an earlier build.
    """
    repo = checkout_of(install_dir)
    if repo is None:
        return None
    record = {
        **checkout_state(repo),
        "built_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "complete": complete,
        "cli": __version__,
    }
    try:
        (install_dir / BUILD_RECORD).write_text(json.dumps(record) + "\n", encoding="utf-8")
    except OSError:
        return None
    return record


def read_build(install_dir: Path) -> dict | None:
    """What :func:`record_build` noted last, or ``None``."""
    try:
        return json.loads((install_dir / BUILD_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _env_value(env_file: Path, key: str) -> str | None:
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def _agent_version(repo: Path | None) -> str | None:
    if repo is None:
        return None
    try:
        text = (repo / "backend/faden_backend/web/api/install/__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    m = _AGENT_VERSION.search(text)
    return m.group(1) if m else None


def _runtimes(repo: Path | None) -> list[str]:
    """The runtimes the server offers its machines, from the runtime manifests it ships."""
    if repo is None:
        return []
    out = []
    for path in sorted((repo / "runtime-image").glob("runtime-manifest*.json")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cert = m.get("certification") if isinstance(m.get("certification"), dict) else {}
        status = cert.get("overall_status") or m.get("overall_status") or "uncertified"
        channel = cert.get("channel")
        out.append(f"{m.get('runtime_id', path.stem)} ({status}{', ' + channel if channel else ''})")
    return out


def describe(install_dir: Path) -> list[tuple[str, str]]:
    """Rows for ``faden version``: what this installation runs."""
    rows: list[tuple[str, str]] = [("Install directory", str(install_dir))]
    if not install_dir.is_dir():
        rows.append(
            ("Installed", "[red]not found -- run 'faden deploy', or set install_dir in faden.yaml[/red]")
        )
        return rows
    release = installed_release(install_dir)
    repo = checkout_of(install_dir)
    if release is not None:
        rows.append(("Installed", f"release {release} (published images)"))
    elif repo is not None:
        state = checkout_state(repo)
        now = state.get("describe") or state.get("commit") or "unknown"
        where = f" on {state['branch']}" if state.get("branch") else " (detached)"
        changed = ", with local changes" if state.get("modified") else ""
        rows.append(("Installed", f"source checkout at {now}{where}{changed}"))
        build = read_build(install_dir)
        if build:
            built = build.get("describe") or build.get("commit") or "unknown"
            notes = []
            if build.get("modified"):
                notes.append("with local changes")
            if build.get("complete") is False:
                notes.append("[yellow]some images failed to build[/yellow]")
            if build.get("commit") != state.get("commit"):
                notes.append("[yellow]checkout has moved since[/yellow]")
            note = f" ({'; '.join(notes)})" if notes else ""
            rows.append(("Images built from", f"{built}, {build.get('built_at', '?')}{note}"))
        else:
            rows.append(
                (
                    "Images built from",
                    "not recorded (built before this was kept; the next upgrade records it)",
                )
            )
    else:
        rows.append(
            ("Installed", "[yellow]unknown (no release marker, not a source checkout)[/yellow]"),
        )

    tag = _env_value(install_dir / ".env", "VERSION")
    rows.append(("Image tag", tag or "latest"))
    agent = _agent_version(repo)
    if agent:
        rows.append(("Machine agent", f"{agent} (handed out by the install line)"))
    for i, runtime in enumerate(_runtimes(repo)):
        rows.append(("Runtimes" if i == 0 else "", runtime))
    return rows
