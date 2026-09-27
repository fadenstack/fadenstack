"""``llmport module`` — enable / disable optional platform modules."""

from __future__ import annotations

import click
from rich.table import Table

from llmport.core.console import console, info, success, warning
from llmport.core.env_gen import set_module_env
from llmport.core.registry import MODULES_COMPAT as MODULES
from llmport.core.settings import LlmportConfig, load_config, save_config


def _write_env(cfg: LlmportConfig, *, enable: list[str], disable: list[str]) -> None:
    """Carry the change into the install's ``.env``, where the services read it.

    Recording a module in llmport.yaml only starts its containers; the
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
    """Manage optional llm.port modules (pii, mcp, skills)."""


@module_group.command("list")
def module_list() -> None:
    """List available modules and their status."""
    cfg = load_config()
    active_profiles = set(cfg.profiles or [])

    table = Table(
        title="llm.port Modules",
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

    Example: llmport module enable pii auth
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
        "\n[dim]Run [bold]llmport up[/bold] to apply changes "
        "(or [bold]llmport dev up[/bold] in a dev workspace).[/dim]",
    )


@module_group.command("disable")
@click.argument("modules", nargs=-1, required=True)
def module_disable(modules: tuple[str, ...]) -> None:
    """Disable one or more modules.

    Example: llmport module disable pii
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
        "\n[dim]Run [bold]llmport down && llmport up[/bold] to apply changes "
        "(or [bold]llmport dev up[/bold] in a dev workspace).[/dim]",
    )
