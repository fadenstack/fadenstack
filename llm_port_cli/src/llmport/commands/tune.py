"""``llmport tune`` — auto-detect system resources and generate scalability settings."""

from __future__ import annotations

from pathlib import Path

import click

from llmport.core.settings import load_config
from llmport.core.tune import tune_env


@click.command("tune")
@click.option(
    "--profile",
    type=click.Choice(["dev", "prod"]),
    default=None,
    help="Tuning profile: dev (conservative) or prod (production-grade). "
    "Default: the one the install was last tuned with, else its environment's.",
)
@click.option(
    "--resource-pct",
    type=click.FloatRange(0.1, 1.0),
    default=None,
    help="Fraction of host CPU to allocate to the control plane (prod only). "
    "Default: the last one used, else 0.25.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show computed values without writing to .env.",
)
def tune_cmd(*, profile: str | None, resource_pct: float | None, dry_run: bool) -> None:
    """Detect host resources and write optimal scalability settings to .env."""
    cfg = load_config()
    tune_env(Path(cfg.install_dir) / ".env", profile=profile, resource_pct=resource_pct, dry_run=dry_run)
