"""``faden maintenance`` — show the maintenance page instead of the platform.

Examples:
    faden maintenance on                          # every page says "under maintenance"
    faden maintenance on -m "Back at 15:00"       # ... with a note for the users
    faden maintenance status
    faden maintenance off
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from faden_cli.core import maintenance
from faden_cli.core.console import console, error, info, success
from faden_cli.core.settings import load_config


def _install_dir() -> Path:
    cfg = load_config()
    if not cfg.install_dir:
        error(
            "No install directory configured.\n"
            "  Run 'faden deploy' first, or set install_dir in faden.yaml."
        )
        sys.exit(1)
    return cfg.install_path


@click.group("maintenance")
def maintenance_group() -> None:
    """Switch the maintenance page on or off.

    While it is on, nginx answers every page with a maintenance page that
    reloads itself once maintenance ends, and the APIs (/api, /v1) with 503.
    The services keep running; only the front door changes. `faden upgrade`
    does this by itself for its restart.
    """


@maintenance_group.command("on")
@click.option("-m", "--message", default=None, help="A note shown on the page, e.g. when it will be back.")
def on_cmd(*, message: str | None) -> None:
    """Answer every page with the maintenance page."""
    install_dir = _install_dir()
    path = maintenance.enter(install_dir, reason="manual", message=message)
    success(f"Maintenance on: every page now shows the maintenance page ({path}).")
    info("Switch it off with: faden maintenance off")


@maintenance_group.command("off")
def off_cmd() -> None:
    """Back to the platform."""
    if maintenance.leave(_install_dir()):
        success("Maintenance off: the platform answers again.")
    else:
        info("Maintenance was not on.")


@maintenance_group.command("status")
def status_cmd() -> None:
    """Whether the maintenance page is up, since when and why."""
    found = maintenance.state(_install_dir())
    if found is None:
        console.print("Maintenance: [green]off[/green]")
        return
    console.print(
        f"Maintenance: [yellow]on[/yellow] ({found.get('reason', 'manual')}, since {found.get('since', '?')})"
    )
    if found.get("message"):
        console.print(f"  Note: {found['message']}")
