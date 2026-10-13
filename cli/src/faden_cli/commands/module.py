"""``faden module`` — enable / disable optional platform modules."""

from __future__ import annotations

import click
from rich.table import Table

from faden_cli.core.console import console, info, success, warning
from faden_cli.core.env_gen import set_module_env
from faden_cli.core.registry import MODULES_COMPAT as MODULES
from faden_cli.core.settings import FadenConfig, load_config, save_config


def _write_env(cfg: FadenConfig, *, enable: list[str], disable: list[str]) -> None:
    """Carry the change into the install's ``.env``, where the services read it.

    Recording a module in faden.yaml only starts its containers; the
    gateway and the backend switch it on from ``.env``.
    """
    env_path = cfg.install_path / ".env"
    if not env_path.exists():
        return
    changed = set_module_env(env_path, enable=enable, disable=disable)
    if changed:
        info(f"Updated {env_path}: {', '.join(sorted(changed))}")


@click.group("module")
def module_group() -> None:
    """Manage optional Fadenstack modules (pii, mcp, skills)."""


@module_group.command("list")
def module_list() -> None:
    """List available modules and their status."""
    cfg = load_config()
    active_profiles = set(cfg.profiles or [])

    table = Table(
        title="Fadenstack Modules",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Module", style="bold")
    table.add_column("Status")
    table.add_column("Description")

    for name, module in MODULES.items():
        enabled = module["profile"] in active_profiles
        status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        table.add_row(name, status, module["description"])

    console.print(table)


@module_group.command("enable")
@click.argument("modules", nargs=-1, required=True)
def module_enable(modules: tuple[str, ...]) -> None:
    """Enable one or more modules.

    Example: faden module enable pii auth
    """
    cfg = load_config()
    profiles = set(cfg.profiles or [])
    enabled: list[str] = []

    for mod in modules:
        mod_lower = mod.lower()
        if mod_lower not in MODULES:
            warning(f"Unknown module: {mod_lower}. Available: {', '.join(MODULES)}")
            continue
        profile = MODULES[mod_lower]["profile"]
        profiles.add(profile)
        enabled.append(mod_lower)
        success(f"Enabled module: {mod_lower}")

    cfg.profiles = sorted(profiles)
    save_config(cfg)
    _write_env(cfg, enable=enabled, disable=[])
    console.print(
        "\n[dim]Run [bold]faden up[/bold] to apply changes "
        "(or [bold]faden dev up[/bold] in a dev workspace).[/dim]",
    )


@module_group.command("disable")
@click.argument("modules", nargs=-1, required=True)
def module_disable(modules: tuple[str, ...]) -> None:
    """Disable one or more modules.

    Example: faden module disable pii
    """
    cfg = load_config()
    profiles = set(cfg.profiles or [])
    disabled: list[str] = []

    for mod in modules:
        mod_lower = mod.lower()
        if mod_lower not in MODULES:
            warning(f"Unknown module: {mod_lower}. Available: {', '.join(MODULES)}")
            continue
        profile = MODULES[mod_lower]["profile"]
        profiles.discard(profile)
        disabled.append(mod_lower)
        success(f"Disabled module: {mod_lower}")

    cfg.profiles = sorted(profiles)
    save_config(cfg)
    _write_env(cfg, enable=[], disable=disabled)
    console.print(
        "
[dim]Run [bold]faden up[/bold] to apply changes: it stops and removes the containers "
        "of a disabled module and keeps its data "
        "(or [bold]faden dev up[/bold] in a dev workspace).[/dim]",
    )
