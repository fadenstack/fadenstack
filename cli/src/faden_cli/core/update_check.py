"""Telling the operator, from the CLI, that a newer Fadenstack release is out.

The way gh and pip do it: every command reads what the last check found
(``update-check.json`` next to faden.yaml); when that is a day old, a thread
fetches the release feed (release_feed.FEED_URL) while the command runs, and
the command ends with one line on stderr if a newer release exists. A
command never waits for the network beyond a moment at its end, and an
offline server just hears nothing.

Fetching the feed sends nothing about this server: it is a plain GET of a
public file, compared here. Off with ``update_check: false`` in faden.yaml
(``faden deploy`` asks) or FADEN_NO_UPDATE_CHECK=1; never in CI or when
stderr is not a terminal (timers, scripts).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from faden_cli import __version__
from faden_cli.core import release_feed

if TYPE_CHECKING:
    import click

    from faden_cli.core.settings import FadenConfig

CACHE_NAME = "update-check.json"
#: How old the last check may be before the next.
STALE = timedelta(hours=24)
FETCH_TIMEOUT_S = 3.0
#: The most a command waits at its end for a check it started.
JOIN_BUDGET_S = 1.5
ENV_OFF = "FADEN_NO_UPDATE_CHECK"
#: Commands that say more themselves (version, doctor, upgrade) or are not about an install (dev).
QUIET_COMMANDS = frozenset({"upgrade", "version", "doctor", "dev"})
USER_AGENT = "fadenstack-update-check"


def installer() -> str:
    """How this CLI was installed: pipx, uv, pip, binary, or source (a checkout)."""
    if getattr(sys, "frozen", False):
        return "binary"
    prefix = Path(sys.prefix).as_posix().lower()
    if "/pipx/" in prefix:
        return "pipx"
    if "/uv/tools/" in prefix:
        return "uv"
    if (Path(__file__).resolve().parents[3] / "pyproject.toml").is_file():
        return "source"
    return "pip"


def enabled(cfg: FadenConfig) -> bool:
    """Whether this CLI checks for releases at all."""
    if os.environ.get(ENV_OFF, "").strip().lower() not in ("", "0", "false", "no"):
        return False
    return bool(cfg.update_check)


def cache_path() -> Path:
    """Where the last check is kept: beside faden.yaml."""
    from faden_cli.core.settings import config_path  # noqa: PLC0415

    return config_path().parent / CACHE_NAME


def feed_url(cfg: FadenConfig) -> str:
    """The feed this CLI reads: the configured mirror, else fadenstack.com."""
    return (cfg.update_feed_url or "").strip() or release_feed.FEED_URL


def _read_cache() -> dict[str, Any]:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(data: dict[str, Any]) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # a read-only home: the check runs again next time


def _checked_at(data: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(data["checked_at"]))
    except (KeyError, ValueError):
        return None


def is_stale(now: datetime | None = None) -> bool:
    """Whether the last check (or failed attempt) is older than :data:`STALE`."""
    checked = _checked_at(_read_cache())
    return checked is None or (now or datetime.now(UTC)) - checked >= STALE


def cached_feed() -> release_feed.Feed | None:
    """The feed the last successful check kept, verified again; None if there is none."""
    data = _read_cache()
    try:
        return release_feed.verify(data["index"].encode(), data["signature"].encode())
    except (KeyError, AttributeError, release_feed.FeedError):
        return None


def fetch(url: str, *, timeout: float = FETCH_TIMEOUT_S) -> tuple[bytes, bytes]:
    """The feed and its signature, as published at *url*."""
    import httpx  # noqa: PLC0415

    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        index = client.get(url)
        index.raise_for_status()
        signature = client.get(release_feed.signature_url(url))
        signature.raise_for_status()
    return index.content, signature.content


def refresh(cfg: FadenConfig, *, timeout: float = FETCH_TIMEOUT_S) -> release_feed.Feed:
    """Fetch and verify the feed now, and keep it. Raises on failure (and remembers the attempt)."""
    previous = _read_cache()
    now = datetime.now(UTC)
    try:
        index, signature = fetch(feed_url(cfg), timeout=timeout)
        feed = release_feed.verify(index, signature)
        seen = cached_feed()
        release_feed.not_older(feed, seen.generated_at if seen else None)
    except Exception as exc:
        # Tried: the next try waits a day too, so an offline server is not asked on every command.
        _write_cache({**previous, "checked_at": now.isoformat(), "error": str(exc) or type(exc).__name__})
        raise
    _write_cache({
        "checked_at": now.isoformat(),
        "url": feed_url(cfg),
        "index": index.decode(),
        "signature": signature.decode(),
    })
    return feed


def current_release(cfg: FadenConfig) -> str | None:
    """The release the install this CLI looks after runs; None without one made from a release."""
    if not cfg.install_dir:
        return None
    from faden_cli.core.bundle import installed_release  # noqa: PLC0415

    try:
        return installed_release(cfg.install_path)
    except OSError:
        return None


def message(feed: release_feed.Feed, cfg: FadenConfig) -> str | None:
    """What to tell the operator, in one or two lines; None when there is nothing to tell."""
    installed = current_release(cfg)
    # The CLI is already newer than the install: the step left is the upgrade itself.
    if installed and release_feed.is_newer(__version__, installed):
        return f"This CLI is {__version__}; the install still runs {installed}. Upgrade it: faden upgrade"
    assessment = release_feed.assess(feed, __version__, cfg.update_channel)
    if not assessment.update_available or assessment.latest is None:
        if assessment.unsupported:
            return f"Fadenstack {__version__} no longer gets fixes (since {assessment.supported_until})."
        return None
    urgency = {
        "critical": " -- a critical fix: upgrade now",
        "security": " -- it fixes security problems",
    }.get(assessment.severity, "")
    steps = release_feed.upgrade_commands(installer(), assessment)
    lines = [f"Fadenstack {assessment.latest.version} is available (you have {__version__}){urgency}."]
    if assessment.path and len(assessment.path) > 1:
        lines.append(f"Upgrade one release at a time: {' -> '.join(assessment.path)}.")
    if steps:
        lines.append(f"Upgrade: {steps[0]}, then faden upgrade")
    return "\n".join(lines)


def interactive() -> bool:
    """Whether someone watches stderr: a terminal, and not CI."""
    try:
        return sys.stderr.isatty() and not os.environ.get("CI")
    except (AttributeError, ValueError):
        return False


def start(ctx: click.Context, *, quiet: bool) -> None:
    """Check in the background while the command runs, and tell the operator at its end."""
    command = ctx.invoked_subcommand
    if quiet or command is None or command in QUIET_COMMANDS or not interactive():
        return
    from faden_cli.core.settings import load_config  # noqa: PLC0415

    try:
        cfg = load_config()
    except Exception:  # a broken faden.yaml is the command's to report
        return
    if not enabled(cfg):
        return
    started = time.monotonic()
    worker: threading.Thread | None = None
    if is_stale():
        worker = threading.Thread(
            target=_refresh_quietly, args=(cfg,), name="faden-update-check", daemon=True
        )
        worker.start()

    def tell() -> None:
        if worker is not None:
            worker.join(max(0.0, JOIN_BUDGET_S - (time.monotonic() - started)))
        feed = cached_feed()
        text = message(feed, cfg) if feed is not None else None
        if text:
            from rich.console import Console  # noqa: PLC0415

            Console(stderr=True).print(f"\n[yellow]{text}[/yellow]", highlight=False)

    ctx.call_on_close(tell)


def _refresh_quietly(cfg: FadenConfig) -> None:
    # An offline server hears nothing; the attempt is remembered.
    with contextlib.suppress(Exception):
        refresh(cfg)
