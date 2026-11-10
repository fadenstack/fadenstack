"""``faden restore`` — restore from a backup directory.

Examples:
    faden restore backups/20250115T120000Z
    faden restore backups/20250115T120000Z --verify-only
    faden restore backups/20250115T120000Z --db-only
    faden restore backups/20250115T120000Z --skip-env
    faden restore backups/20250115T120000Z --key-file /media/usb/fadenstack-recovery-key.txt

Each database becomes exactly the backup's. While the install keeps running, every database of the backup is
restored beside the live one (``<db>__restore``); a pg_restore error stops the restore there, with the live
databases untouched (core/restore_swap.py). Then, behind the maintenance page, the services stop, one
transaction swaps the names (live → ``<db>__old``, restored → live), the backup's ``.env``, volumes and
certificate material go in -- what they replace kept first -- and everything starts again. Healthy: the old
databases are dropped, "Restore complete". Not healthy: everything goes back as it was, and the restore exits 1.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

from faden_cli.core import backup_crypto, backup_key, maintenance, restore_swap
from faden_cli.core.backup import (
    FilesNotKept,
    Kept,
    Prepared,
    RestoreResult,
    describe_release,
    keep_before_restore,
    prepare_restore,
    put_back,
    read_manifest,
    restore_env,
    restore_log_path,
    restore_stamp,
    restore_tls,
    restore_volumes,
    verify_backup,
)
from faden_cli.core.backup import data_dir_of
from faden_cli.core.compose import (
    ComposeContext,
    build_context_from_config,
    ps as compose_ps,
    restart as compose_restart,
    service_names,
    stop as compose_stop,
    up as compose_up,
)
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.private_files import open_private
from faden_cli.core.settings import FadenConfig, load_config
from faden_cli.commands.deploy import _regenerate_rmq_definitions, _sync_postgres_password


@click.command("restore")
@click.argument("backup_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--db-only", is_flag=True, default=False, help="Restore databases only.")
@click.option("--skip-env", is_flag=True, default=False, help="Don't overwrite the current .env.")
@click.option(
    "--verify-only",
    is_flag=True,
    default=False,
    help="Validate checksums without restoring.",
)
@click.option(
    "--key-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="The recovery key, for an encrypted backup; otherwise it is asked for.",
)
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def restore_cmd(
    backup_dir: str,
    *,
    db_only: bool,
    skip_env: bool,
    verify_only: bool,
    key_file: Path | None,
    yes: bool,
) -> None:
    """Restore Fadenstack from a backup directory.

    BACKUP_DIR is the path to a timestamped backup folder containing
    a manifest.json file created by ``faden backup``. An encrypted backup
    is opened with the recovery key first.
    """
    console.print("\n[bold magenta]fadenstack — Restore[/bold magenta]\n")

    backup_path = Path(backup_dir).resolve()
    if not backup_crypto.is_encrypted(backup_path):
        _restore_cmd(backup_path, backup_path, db_only=db_only, skip_env=skip_env, verify_only=verify_only, yes=yes)
        return
    opened = _open(backup_path, key_file)
    try:
        _restore_cmd(backup_path, opened, db_only=db_only, skip_env=skip_env, verify_only=verify_only, yes=yes)
    finally:
        backup_crypto.close_backup(backup_path)


#: Recovery keys typed wrong before the restore stops.
_KEY_ATTEMPTS = 3


def _open(backup_path: Path, key_file: Path | None) -> Path:
    """Decrypt the encrypted backup with the recovery key: from --key-file, still on the server, or asked."""
    from faden_cli.commands.backup import key_text  # noqa: PLC0415

    index = backup_crypto.read_index(backup_path) or {}
    info(f"This backup is encrypted to the backup key {index.get('key_id') or 'unknown'}.")
    cfg = load_config()
    left = backup_key.pending(cfg.install_path) if cfg.install_dir else None
    given = key_file or left
    if left is not None and key_file is None:
        info(f"Using the recovery key still on this server ({left}).")
    for attempt in range(1, _KEY_ATTEMPTS + 1):
        text = key_text(given.read_text(encoding="utf-8")) if given else click.prompt("Recovery key", hide_input=True)
        try:
            key = backup_crypto.RecoveryKey.from_text(text)
            info("Opening the backup…")
            return backup_crypto.open_backup(backup_path, key)
        except (backup_crypto.KeyTextError, backup_crypto.OpenError) as exc:
            error(f"The backup does not open with that key: {exc}")
            if given or attempt == _KEY_ATTEMPTS:
                sys.exit(1)
    sys.exit(1)


def _restore_cmd(
    backup_path: Path, source: Path, *, db_only: bool, skip_env: bool, verify_only: bool, yes: bool,
) -> None:
    """Check the backup in *source* (the backup itself, or its decrypted copy) and restore it."""
    manifest = read_manifest(source)
    if manifest is None:
        error(f"No manifest.json found in {source}")
        sys.exit(1)

    # ── Show backup info ──────────────────────────────────────
    console.print(f"  Backup date:    {manifest.get('created_at', 'unknown')}")
    console.print(f"  Release:        {describe_release(manifest)}")
    db_names = list(manifest.get("databases", {}).keys())
    console.print(f"  Databases:      {', '.join(db_names)}")
    if manifest.get("env_snapshot"):
        console.print(f"  .env snapshot:  {manifest['env_snapshot']}")
    if manifest.get("volumes"):
        console.print(f"  Volumes:        {', '.join(manifest['volumes'])}")
    console.print()

    # ── Verify checksums ──────────────────────────────────────
    info("Verifying backup checksums…")
    check_errors = verify_backup(source, manifest)
    if check_errors:
        for msg in check_errors:
            error(f"  {msg}")
        sys.exit(1)
    success("Checksums OK.")

    if verify_only:
        return

    # ── Confirm ───────────────────────────────────────────────
    if not yes:
        console.print()
        warning("This replaces every database the backup holds with the backup's, exactly.")
        click.confirm("Proceed with restore?", default=False, abort=True)

    cfg = load_config()
    ctx = build_context_from_config(cfg)
    install = Path(str(ctx.project_dir)) if ctx.project_dir else Path.cwd()
    stamp = restore_stamp()
    log_path = restore_log_path(install, stamp)
    info(f"pg_restore's whole output goes to {log_path}")

    # ── 1. Beside the running install: nothing it runs on changes ──
    console.print("\n[bold cyan]Restoring the databases beside the live ones…[/bold cyan]")
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)
    with open_private(log_path) as log:
        try:
            prepared = prepare_restore(ctx, source, manifest, log, db_only=db_only, work_parent=install)
        except restore_swap.RestoreRefused as exc:
            error(str(exc))
            sys.exit(1)
        except (restore_swap.RestoreFailed, restore_swap.PostgresError) as exc:
            error(f"The restore failed: {exc}.")
            error(f"  Nothing was changed: the live databases and the services are as they were. "
                  f"pg_restore's whole output: {log_path}")
            sys.exit(1)
    success(f"Restored beside the live databases: {', '.join(prepared.names)}.")
    if prepared.not_in_backup:
        info(f"Not in the backup, left as they are: {', '.join(prepared.not_in_backup)}.")

    # ── 2. Swap in, behind the maintenance page ───────────────
    # The front door stays up and tells users the platform is being worked on, instead of refusing connections
    # until the end. Lifted however this ends. nginx holds none of the volumes a restore writes.
    with maintenance.during(install, reason="restore"):
        outcome = _swap_in(ctx, cfg, source, manifest, prepared, install / f".restore-{stamp}",
                           db_only=db_only, skip_env=skip_env)

    console.print()
    if not outcome.ok:
        for line in outcome.messages:
            error(line)
        error(f"  pg_restore's whole output: {log_path}")
        sys.exit(1)
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    result = outcome.result
    record_event(
        "backup.restore", "install", Path(str(backup_path)).name,
        metadata={
            "databases": list(result.databases_restored or []), "db_only": db_only,
            "encrypted": source != backup_path,
        },
    )
    if outcome.left:
        error("The backup is restored and every service is healthy, but these old databases could not be dropped:")
        for line in outcome.left:
            error(f"  {line}")
        error(f"  Drop them with: docker exec {restore_swap.container()} psql -U postgres "
              + " ".join(f'-c "DROP DATABASE IF EXISTS {restore_swap.quote(name.split(" ", 1)[0])};"'
                         for name in outcome.left))
        sys.exit(1)
    success("Restore complete.")
    console.print(f"  Databases: {', '.join(result.databases_restored)} (exactly the backup's; the ones before dropped)")
    if result.env_restored:
        console.print("  .env: restored")
    if result.volumes_restored:
        console.print(f"  Volumes: {', '.join(result.volumes_restored)}")
    if result.tls_restored:
        console.print(f"  Certificate material: {len(result.tls_restored)} file(s); the machines still trust it")
    console.print(f"  Log: {log_path}")


#: The front door, which keeps running (and serving the maintenance page), and the database server, which
#: renames its databases: neither is stopped for the swap.
_FRONT = "nginx"
_POSTGRES = "postgres"
#: How long the services may take to become healthy after the swap, as for an upgrade.
_HEALTHY_WITHIN = 180


@dataclass
class Outcome:
    """How phase 2 ended: *ok* when the restored install is healthy; *left* the old databases that would not drop;
    *messages* what to tell the operator when it is not ok."""

    ok: bool
    result: RestoreResult = field(default_factory=RestoreResult)
    left: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _stop_services(ctx: ComposeContext, names: list[str]) -> int:
    """Stop everything but nginx and Postgres; the containers keep their volumes and networks."""
    running = [name for name in names if name not in (_FRONT, _POSTGRES)]
    return compose_stop(ctx, services=running) if running else 0


def _not_healthy(ctx: ComposeContext) -> list[str]:
    """The services that are not running and healthy (one-off migrators that ended with 0 aside)."""
    found: list[str] = []
    for row in compose_ps(ctx):
        state, health = (row.state or row.status or "").lower(), (row.health or "").lower()
        if state == "exited" and row.exit_code == 0:
            continue
        if state != "running" or health in ("unhealthy", "starting"):
            found.append(f"{row.service or row.name} ({health or state})")
    return found


def _after_env(ctx: ComposeContext, cfg: FadenConfig, env_path: Path) -> None:
    """What follows a new .env: Postgres' password and RabbitMQ's users written from it (both read .env)."""
    from faden_cli.core.env_gen import read_env_file  # noqa: PLC0415

    info("Syncing Postgres password with the .env…")
    _sync_postgres_password(ctx, env_path)
    # RabbitMQ makes its users from definitions.json on every start; the services log in with the passwords in
    # .env. The file still held those of the .env the restore replaced: RabbitMQ refused the backend and the
    # gateway (ACCESS_REFUSED), and the install did not come back. `faden upgrade` writes it the same way.
    _regenerate_rmq_definitions(Path(str(ctx.project_dir)), read_env_file(env_path), cfg.profiles)


def _swap_in(
    ctx: ComposeContext,
    cfg: FadenConfig,
    source: Path,
    manifest: dict,
    prepared: Prepared,
    work_dir: Path,
    *,
    db_only: bool,
    skip_env: bool,
) -> Outcome:
    """Phase 2: stop, swap, put the backup's files in, start; healthy → drop the old databases, else go back."""
    console.print("\n[bold cyan]Stopping application services…[/bold cyan]")
    names = service_names(ctx)
    copies = [name + restore_swap.RESTORE_SUFFIX for name in prepared.names]
    if _stop_services(ctx, names) != 0:
        left = restore_swap.drop(copies)
        started = compose_up(ctx, detach=True, wait=True, timeout=_HEALTHY_WITHIN)
        return Outcome(False, messages=[
            "The services could not be stopped, so nothing was swapped: the live databases are as they were.",
            *([f"  These copies could not be dropped: {'; '.join(left)}"] if left else []),
            "  The services were started again" + ("." if started == 0 else f", not all of them (exit {started})."),
        ])
    compose_up(ctx, services=[_POSTGRES], detach=True, wait=True, timeout=60)

    console.print("\n[bold cyan]Swapping the restored databases in…[/bold cyan]")
    try:
        swapped = restore_swap.swap_in(prepared.names, prepared.live)
    except restore_swap.SwapFailed as exc:
        left = restore_swap.drop(copies)
        started = compose_up(ctx, detach=True, wait=True, timeout=_HEALTHY_WITHIN)
        return Outcome(False, messages=[
            f"The databases could not be swapped ({exc}); nothing was renamed, the live databases are as they were.",
            *([f"  These copies could not be dropped: {'; '.join(left)}"] if left else []),
            "  The services were started again" + ("." if started == 0 else f", not all of them (exit {started})."),
        ])
    success(f"Swapped in: {', '.join(prepared.names)} (the ones before kept as <name>{restore_swap.OLD_SUFFIX}).")
    result = RestoreResult(databases_restored=list(prepared.names))

    try:
        kept = keep_before_restore(ctx, manifest, work_dir, db_only=db_only, skip_env=skip_env,
                                   volumes=prepared.volumes)
    except (FilesNotKept, OSError) as exc:
        shutil.rmtree(work_dir, ignore_errors=True)  # partial copies; nothing of the install was replaced yet
        return _go_back(ctx, cfg, names, swapped, None, result, f"What the restore replaces could not be kept: {exc}")

    # The backup's files: .env, the volumes, the certificate material.
    if kept.env_path is not None:
        console.print("\n[bold cyan]Restoring .env…[/bold cyan]")
        result.env_restored = restore_env(source, kept.env_path)
    failed: list[str] = []
    if not db_only and manifest.get("volumes"):
        console.print("\n[bold cyan]Restoring Docker volumes…[/bold cyan]")
        result.volumes_restored = restore_volumes(source, manifest, data_dir=data_dir_of(ctx.env_file), ctx=ctx,
                                                  failed=failed)
    if not db_only and manifest.get("tls_material") and ctx.project_dir:
        console.print("\n[bold cyan]Restoring the certificate material…[/bold cyan]")
        result.tls_restored = restore_tls(source, Path(str(ctx.project_dir)), list(manifest["tls_material"]))
    if kept.env_path is not None and not result.env_restored:
        failed.append(".env")
    if failed:
        return _go_back(ctx, cfg, names, swapped, kept, result, f"Not restored: {', '.join(failed)}")

    if result.env_restored:
        env_path = kept.env_path or Path(str(ctx.env_file))
        # A .env backed up before a release required it may lack a key the compose file now requires.
        from faden_cli.core.env_gen import fill_required_env  # noqa: PLC0415

        for note in fill_required_env(env_path, profiles=list(cfg.profiles)):
            warning(note)
        _after_env(ctx, cfg, env_path)
        success("RabbitMQ users written from the restored .env.")
    # The provenance signer of a .env from before the module, or issued by an authority the restore replaced.
    if (result.env_restored or result.tls_restored) and ctx.env_file and ctx.project_dir:
        from faden_cli.core.provenance_keys import ensure_step as provenance_keys_step  # noqa: PLC0415

        provenance_keys_step(Path(ctx.project_dir), Path(str(ctx.env_file)))

    console.print("\n[bold cyan]Starting all services…[/bold cyan]")
    started = compose_up(ctx, detach=True, wait=True, timeout=_HEALTHY_WITHIN)
    if started != 0:
        sick = _not_healthy(ctx)
        why = f"The restored install did not become healthy (docker compose up exited with {started})"
        return _go_back(ctx, cfg, names, swapped, kept, result, why + (f": {', '.join(sick)}" if sick else ""))
    # nginx read the certificate and picked plain HTTP or HTTPS when it started; a restored one is only served
    # after it starts again.
    if result.tls_restored and _FRONT in names:
        compose_restart(ctx, services=[_FRONT])
    left = restore_swap.drop_old(swapped)
    kept.discard()
    return Outcome(True, result=result, left=left)


def _go_back(
    ctx: ComposeContext,
    cfg: FadenConfig,
    names: list[str],
    swapped: list[restore_swap.Swapped],
    kept: Kept | None,
    result: RestoreResult,
    why: str,
) -> Outcome:
    """Put everything back as before the restore and start it; says exactly what did not go back."""
    console.print(f"\n[bold red]{why}.[/bold red] [bold cyan]Putting everything back as it was…[/bold cyan]")
    problems: list[str] = []
    if _stop_services(ctx, names) != 0:
        problems.append("the services could not all be stopped first")
    compose_up(ctx, services=[_POSTGRES], detach=True, wait=True, timeout=60)
    try:
        left = restore_swap.swap_back(swapped)
        problems.extend(f"the restored copy {line} could not be dropped" for line in left)
    except restore_swap.SwapFailed as exc:
        problems.append(
            f"the databases: still the backup's ({exc}); the ones before are "
            + ", ".join(s.name + restore_swap.OLD_SUFFIX for s in swapped if s.had_live)
        )
    if kept is not None:
        problems.extend(put_back(kept, ctx))
        if kept.env_path is not None and kept.env is not None:
            _after_env(ctx, cfg, kept.env_path)
    started = compose_up(ctx, detach=True, wait=True, timeout=_HEALTHY_WITHIN)
    if result.tls_restored and _FRONT in names:
        compose_restart(ctx, services=[_FRONT])
    messages = [f"{why}."]
    if problems:
        messages.append("Not everything went back as it was before the restore:")
        messages.extend(f"  - {line}" for line in problems)
        if kept is not None:
            messages.append(f"  What the restore replaced is kept in {kept.work_dir}.")
    else:
        what = "the databases" + (", .env, the volumes and the certificate material" if kept is not None else "")
        messages.append(f"Everything the restore changed went back: {what} are as they were before the restore.")
        if kept is not None:
            kept.discard()
    messages.append(
        "The install as it was before the restore is running again."
        if started == 0
        else f"The install as it was before the restore did not start either (docker compose up exited with "
             f"{started}): see `faden status`, and why with `faden logs <service>`."
    )
    return Outcome(False, result=result, messages=messages)
