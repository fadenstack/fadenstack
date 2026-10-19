"""``faden module`` — enable / disable optional platform modules."""

from __future__ import annotations

import getpass
import os
from datetime import UTC, datetime

import click
from rich.table import Table

from faden_cli.core.console import console, info, success, warning
from faden_cli.core.env_gen import set_module_env
from faden_cli.core.registry import MODULES as MODULE_INFO
from faden_cli.core.registry import MODULES_COMPAT as MODULES
from faden_cli.core.settings import FadenConfig, config_path, load_config, save_config


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


def default_modules(cfg: FadenConfig, profiles: set[str] | list[str] | None = None) -> list[str]:
    """The modules on by default that are not on yet, less those an administrator switched off.

    *profiles* are the ones on so far (the config's when not given). ``faden deploy`` and
    ``faden upgrade`` switch these on.
    """
    active = set(cfg.profiles or []) if profiles is None else set(profiles)
    switched_off = cfg.modules_switched_off or {}
    return [
        name
        for name, module in MODULE_INFO.items()
        if module.on_by_default and module.profile not in active and name not in switched_off
    ]


def _os_user() -> str:
    """Who runs this command: the operating system's account name."""
    try:
        return getpass.getuser()
    except (ImportError, KeyError, OSError):  # no login name, e.g. a container user without a passwd entry
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def record_switched_off(cfg: FadenConfig, name: str) -> dict[str, str]:
    """Note in *cfg* that *name* was switched off, by whom and when; returns the note."""
    note = {"by": _os_user(), "at": datetime.now(tz=UTC).isoformat(timespec="seconds")}
    cfg.modules_switched_off = {**(cfg.modules_switched_off or {}), name: note}
    return note


@click.group("module")
def module_group() -> None:
    """Manage optional Fadenstack modules (pii, mcp, skills, provenance)."""


@module_group.command("list")
def module_list() -> None:
    """List available modules and their status."""
    cfg = load_config()
    active_profiles = set(cfg.profiles or [])
    switched_off = cfg.modules_switched_off or {}

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
        note = switched_off.get(name)
        if not enabled and note:
            status += f" [dim](by {note.get('by', '?')}, {note.get('at', '?')})[/dim]"
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
        # The note that an administrator switched it off goes: deploy and upgrade no longer leave it off.
        cfg.modules_switched_off = {
            name: note for name, note in (cfg.modules_switched_off or {}).items() if name != mod_lower
        }
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
@click.option("--yes", "-y", is_flag=True, help="Switch off without asking, for scripts.")
def module_disable(modules: tuple[str, ...], *, yes: bool = False) -> None:
    """Disable one or more modules.

    A module that is on by default (provenance) says what switching it off
    loses and asks first. faden.yaml records who switched it off and when,
    and deploy and upgrade leave it off until it is enabled again.

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
        if mod_lower not in disabled:
            disabled.append(mod_lower)

    # Asked before anything changes: declined, every module given stays as it was.
    warned = [name for name in disabled if name in MODULE_INFO and MODULE_INFO[name].off_warning]
    for name in warned:
        warning(f"Switching off {name}: {MODULE_INFO[name].off_warning}")
    if warned and not yes:
        click.confirm(f"Switch off {', '.join(warned)}?", default=False, abort=True)

    recorded: dict[str, dict[str, str]] = {}
    for name in disabled:
        profiles.discard(MODULES[name]["profile"])
        if name in MODULE_INFO and MODULE_INFO[name].on_by_default:
            recorded[name] = record_switched_off(cfg, name)
        success(f"Disabled module: {name}")

    cfg.profiles = sorted(profiles)
    save_config(cfg)
    _write_env(cfg, enable=[], disable=disabled)
    for name, note in recorded.items():
        info(
            f"Recorded in {config_path()}: {name} switched off by {note['by']} at {note['at']}. "
            f"Upgrades leave it off; faden module enable {name} switches it on again."
        )
    console.print(
        "\n[dim]Run [bold]faden up[/bold] to apply changes: it stops and removes the containers "
        "of a disabled module and keeps its data "
        "(or [bold]faden dev up[/bold] in a dev workspace).[/dim]",
    )
