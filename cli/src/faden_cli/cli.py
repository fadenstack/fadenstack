"""CLI entry point — Click root group and subcommand registration.

This module defines the ``faden`` command group and wires up all
subcommands.  Run ``faden --help`` for the full command tree.
"""

from __future__ import annotations

import sys

import click

from faden_cli import __version__


class AliasedGroup(click.Group):
    """Click group that supports command abbreviations.

    For instance ``faden st`` resolves to ``faden status`` if no
    other command starts with ``st``.
    """

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        matches = [x for x in self.list_commands(ctx) if x.startswith(cmd_name)]
        if not matches:
            return None
        if len(matches) == 1:
            return click.Group.get_command(self, ctx, matches[0])
        ctx.fail(f"Ambiguous command '{cmd_name}'. Did you mean: {', '.join(sorted(matches))}?")
        return None  # unreachable, but satisfies type checker


@click.group(cls=AliasedGroup, invoke_without_command=True)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress informational output.")
@click.version_option(version=__version__, prog_name="faden")
@click.pass_context
def cli(ctx: click.Context, *, verbose: bool, quiet: bool) -> None:
    """faden — CLI installer and management tool for fadenstack."""
    from faden_cli.core.detect import ensure_tool_path

    # Make tools installed to ~/.local/bin / ~/.cargo/bin visible to
    # every child subprocess, even when the current shell PATH lacks them
    # (typical for fresh SSH sessions and GUI-launched terminals).
    ensure_tool_path()

    from faden_cli.core.docker_env import ensure_docker_host

    # If the configured Docker endpoint is dead but Rancher Desktop's
    # answers, export DOCKER_HOST so every docker subprocess (detect,
    # compose, logs, …) reaches the live daemon.  No-op otherwise.
    ensure_docker_host(verbose=verbose, quiet=quiet)

    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet

    from faden_cli.core import update_check

    # A newer release: checked while the command runs, said at its end.
    update_check.start(ctx, quiet=quiet)

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── Register subcommands ──────────────────────────────────────────

def register_core_commands(group: click.Group | None = None) -> None:
    """Import and register all core subcommand groups and commands.

    Accepts an optional *group* parameter so the EE CLI can call this
    with its own root group.  Defaults to ``cli`` when called internally.
    """
    target = group or cli

    from faden_cli.commands.version import version_cmd
    from faden_cli.commands.doctor import doctor_cmd
    from faden_cli.commands.status import status_cmd
    from faden_cli.commands.up import up_cmd
    from faden_cli.commands.down import down_cmd
    from faden_cli.commands.logs_cmd import logs_cmd
    from faden_cli.commands.config import config_group
    from faden_cli.commands.module import module_group
    from faden_cli.commands.tune import tune_cmd
    from faden_cli.commands.deploy import deploy_cmd
    from faden_cli.commands.rebuild import rebuild_cmd
    from faden_cli.commands.backup import backup_cmd
    from faden_cli.commands.restore import restore_cmd
    from faden_cli.commands.upgrade import upgrade_cmd
    from faden_cli.commands.dev.dev_group import dev_group
    from faden_cli.commands.admin import admin_group
    from faden_cli.commands.observe import observe_group
    from faden_cli.commands.runtime_images import runtime_images_cmd
    from faden_cli.commands.tls import tls_group
    from faden_cli.commands.ports import ports_cmd
    from faden_cli.commands.maintenance import maintenance_group

    target.add_command(version_cmd, "version")
    target.add_command(doctor_cmd, "doctor")
    target.add_command(status_cmd, "status")
    target.add_command(up_cmd, "up")
    target.add_command(down_cmd, "down")
    target.add_command(logs_cmd, "logs")
    target.add_command(config_group, "config")
    target.add_command(module_group, "module")
    target.add_command(tune_cmd, "tune")
    target.add_command(deploy_cmd, "deploy")
    target.add_command(rebuild_cmd, "rebuild")
    target.add_command(backup_cmd, "backup")
    target.add_command(restore_cmd, "restore")
    target.add_command(upgrade_cmd, "upgrade")
    target.add_command(dev_group, "dev")
    target.add_command(admin_group, "admin")
    target.add_command(observe_group, "observe")
    target.add_command(runtime_images_cmd, "runtime-images")
    target.add_command(tls_group, "tls")
    target.add_command(ports_cmd, "ports")
    target.add_command(maintenance_group, "maintenance")


register_core_commands()


def _use_utf8_output() -> None:
    """Make the console accept the output this CLI actually produces.

    Windows consoles default to cp1252, and this CLI writes arrows, box rules
    and check marks -- some 17,000 non-ASCII characters across its sources.
    Any one of them reaching a cp1252 stream raises UnicodeEncodeError, so
    ``faden dev up --help`` ended in a traceback instead of printing help,
    and redirecting any command to a file did the same.

    ``errors="replace"`` rather than a hard switch: where a terminal genuinely
    cannot represent a glyph, a degraded character is a far better outcome
    than a stack trace standing in for the output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pytest capture, or a plain wrapper
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - already detached
            pass


def main() -> None:
    """Package entry point."""
    _use_utf8_output()
    cli()


if __name__ == "__main__":
    main()
