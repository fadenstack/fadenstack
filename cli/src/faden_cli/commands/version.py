"""``faden version`` — print version and runtime information."""

from __future__ import annotations

import platform
import sys

import click

from faden_cli import __version__
from faden_cli.core import installation
from faden_cli.core.console import console, kv_table
from faden_cli.core.detect import detect_docker
from faden_cli.core.settings import load_config


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
