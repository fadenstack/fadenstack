"""``faden dev doctor`` — check and optionally install prerequisites."""

from __future__ import annotations

import click

from faden_cli.core.console import console, success, warning
from faden_cli.core.install import ensure_prerequisites

from .dev_group import dev_group


@dev_group.command("doctor")
@click.option(
    "--install",
    is_flag=True,
    help="Offer to install missing tools (uv, git, node).",
)
@click.option(
    "--yes", "-y",
    is_flag=True,
    help="Auto-confirm installation without prompting.",
)
def dev_doctor(*, install: bool, yes: bool) -> None:
    """Check development prerequisites and system readiness.

    Verifies that Docker, Git, uv, and Node.js are installed.
    With --install, offers to install missing tools automatically.

    \b
    Examples:
        faden dev doctor
        faden dev doctor --install
        faden dev doctor --install -y
    """
    console.print("\n[bold cyan]Checking prerequisites…[/bold cyan]")
    all_ok = ensure_prerequisites(install=install, auto_confirm=yes)

    if all_ok:
        success("\nAll prerequisites are satisfied.")
    else:
        warning(
            "\nSome prerequisites are missing. "
            "Run [bold]faden dev doctor --install[/bold] to install them."
        )
        raise SystemExit(1)
