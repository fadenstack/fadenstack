"""``faden version`` — print version and runtime information."""

from __future__ import annotations

import platform
import sys

import click

from faden_cli import __version__
from faden_cli.core import installation, release_feed, update_check
from faden_cli.core.console import console, kv_table
from faden_cli.core.detect import detect_docker
from faden_cli.core.settings import FadenConfig, load_config


@click.command("version")
@click.option("--short", is_flag=True, help="Print only the CLI version string.")
def version_cmd(*, short: bool) -> None:
    """Display version and runtime information."""
    if short:
        click.echo(__version__)
        return

    docker = detect_docker()

    rows: list[tuple[str, str]] = [
        ("faden CLI", __version__),
        ("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        ("Platform", platform.platform()),
        ("Docker", docker.version or "[red]not found[/red]"),
        ("Compose", docker.compose_version or "[red]not found[/red]"),
    ]

    kv_table("Fadenstack CLI", rows)
    console.print()

    # What the installation this CLI looks after runs: the CLI's own version says nothing about that.
    cfg = load_config()
    if cfg.install_dir:
        kv_table("Fadenstack installation", installation.describe(cfg.install_path))
        console.print()

    kv_table("Releases", release_rows(cfg))
    console.print()


def release_rows(cfg: FadenConfig) -> list[tuple[str, str]]:
    """What the release feed says about this CLI's release, checked now (cached when offline)."""
    if not update_check.enabled(cfg):
        return [("Update check", "off (update_check in faden.yaml, or FADEN_NO_UPDATE_CHECK)")]
    rows: list[tuple[str, str]] = []
    try:
        feed: release_feed.Feed | None = update_check.refresh(cfg)
    except Exception as exc:  # offline is an answer, not a failure
        feed = update_check.cached_feed()
        rows.append(("Update check", f"[yellow]could not read {update_check.feed_url(cfg)}: {exc}[/yellow]"))
    if feed is None:
        return rows
    found = release_feed.assess(feed, __version__, cfg.update_channel)
    latest = found.latest.version if found.latest else "-"
    rows.append((f"Newest ({found.channel})", latest))
    if not found.update_available:
        rows.append(("Status", "[green]up to date[/green]"))
    else:
        colour = {"critical": "red", "security": "yellow"}.get(found.severity, "cyan")
        rows.append(("Status", f"[{colour}]update available ({found.severity})[/{colour}]"))
        if len(found.path) > 1:
            rows.append(("Path", " -> ".join(found.path)))
        commands = release_feed.upgrade_commands(update_check.upgrade_kind(cfg), found)
        if commands:
            rows.append(("Upgrade", "\n".join(commands)))
        if found.latest and found.latest.notes_url:
            rows.append(("Notes", found.latest.notes_url))
    if found.unsupported:
        until = found.supported_until
        rows.append(("Support", f"[red]{found.current} no longer gets fixes (since {until})[/red]"))
    return rows
