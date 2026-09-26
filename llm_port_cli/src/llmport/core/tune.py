"""Size the services for this host and write it to ``.env``.

The one place that does it: ``llmport tune`` calls it, and so do ``deploy``,
``upgrade`` and ``up --gen-env``. Deploy had its own copy -- the sizing baked
into a newly generated ``.env`` -- so an existing install was never sized
again, and a fix to the sizing reached no one who had deployed already.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from llmport.core.console import console, success
from llmport.core.env_gen import read_env_file, write_env_file
from llmport.core.sysinfo import (
    ALL_SERVICES,
    DB_SERVICES,
    DEFAULT_RESOURCE_PCT,
    RABBIT_SERVICES,
    TuneProfile,
    calculate_tune_profile,
    detect_system,
)

#: The CPU share ``llmport tune --resource-pct`` accepts.
_PCT_MIN, _PCT_MAX = 0.1, 1.0


def _profile_of(env: dict[str, str]) -> str:
    """The profile the install was tuned with, else the one its services run in."""
    if env.get("LLMPORT_TUNE_PROFILE") in {"dev", "prod"}:
        return env["LLMPORT_TUNE_PROFILE"]
    return "dev" if env.get("LLM_PORT_BACKEND_ENVIRONMENT") == "dev" else "prod"


def _resource_pct_of(env: dict[str, str]) -> float:
    try:
        pct = float(env.get("LLMPORT_TUNE_RESOURCE_PCT") or DEFAULT_RESOURCE_PCT)
    except ValueError:
        return DEFAULT_RESOURCE_PCT
    return pct if _PCT_MIN <= pct <= _PCT_MAX else DEFAULT_RESOURCE_PCT


def tune_env(
    env_path: Path,
    *,
    profile: str | None = None,
    resource_pct: float | None = None,
    dry_run: bool = False,
) -> TuneProfile:
    """Size the services for this host, show it, and write it to *env_path*.

    *profile* and *resource_pct* default to what the install was last tuned
    with, so deploy and upgrade keep what ``llmport tune`` chose. The rest of
    the file -- secrets included -- is kept.
    """
    env = read_env_file(env_path)
    profile = profile or _profile_of(env)
    resource_pct = resource_pct or _resource_pct_of(env)
    tp = calculate_tune_profile(profile, system=detect_system(), resource_pct=resource_pct)
    show(tp)
    if dry_run:
        console.print("[dim]Dry run — no files written.[/dim]")
        return tp
    env.update(tp.to_env_dict())
    write_env_file(env_path, env, preserve_secrets=True)
    success(f"Scalability settings written to {env_path}")
    return tp


def show(tp: TuneProfile) -> None:
    """The host, the settings for each service, and the database connections they add up to."""
    sys_info = tp.system
    hw_table = Table(title="Detected Hardware", show_header=False, title_style="bold cyan")
    hw_table.add_column("Metric", style="dim")
    hw_table.add_column("Value", style="bold")
    hw_table.add_row("Physical CPU cores", str(sys_info.physical_cores))
    hw_table.add_row("Logical CPU cores", str(sys_info.logical_cores))
    hw_table.add_row("Total RAM", f"{sys_info.total_ram_gb} GB")
    hw_table.add_row("Profile", tp.profile)
    if tp.profile == "prod":
        hw_table.add_row("Resource budget", f"{int(tp.resource_pct * 100)}% ({tp.budget_cores} cores)")
    console.print(hw_table)
    console.print()

    svc_table = Table(title="Computed Scalability Settings", title_style="bold cyan")
    svc_table.add_column("Service", style="bold")
    svc_table.add_column("Workers", justify="right")
    svc_table.add_column("DB Pool", justify="right")
    svc_table.add_column("DB Overflow", justify="right")
    svc_table.add_column("RMQ Pool", justify="right")
    svc_table.add_column("RMQ Channels", justify="right")
    for svc in ALL_SERVICES:
        svc_table.add_row(
            svc,
            str(tp.workers[svc]),
            str(tp.db_pool_size[svc]) if svc in DB_SERVICES else "—",
            str(tp.db_max_overflow[svc]) if svc in DB_SERVICES else "—",
            str(tp.rabbit_pool_size[svc]) if svc in RABBIT_SERVICES else "—",
            str(tp.rabbit_channel_pool_size[svc]) if svc in RABBIT_SERVICES else "—",
        )
    console.print(svc_table)

    peaks = tp.db_connections()
    console.print(
        f"Database connections at peak: {sum(peaks.values())} "
        f"({', '.join(f'{name} {n}' for name, n in peaks.items())}); "
        f"Postgres max_connections {tp.max_connections}."
    )
    console.print()
