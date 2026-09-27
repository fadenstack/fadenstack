"""``faden tls`` — serve the console, the API and /v1 over HTTPS, or stop.

nginx picks HTTPS or plain HTTP when it starts, from what ``<install>/tls``
holds (shared/nginx/select-config.sh), so every change here ends with
a restart of the proxy -- the only container it touches -- or, when HTTPS stays
on and only the certificate is new, a reload that keeps open connections.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import click

from faden_cli.core import tls
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.edge import console_url, url_for
from faden_cli.core.env_gen import read_env_file
from faden_cli.core.settings import load_config

PROXY_SERVICE = "nginx"
#: A connected agent asks for the authority every minute (agent's TRUST_REFRESH_SEC).
MACHINE_TRUST_WAIT_SEC = 70
PROXY_CONTAINER = "faden-nginx"
RELOAD_COMMAND = "sh /etc/nginx/faden/select-config.sh && nginx -s reload"


def _install_dir() -> Path:
    cfg = load_config()
    if not cfg.install_dir:
        error("No install found. Run faden deploy first.")
        sys.exit(1)
    return Path(cfg.install_dir)


def _proxy_running() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    result = subprocess.run(  # noqa: S603
        [docker, "inspect", "-f", "{{.State.Running}}", PROXY_CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _reload_proxy() -> bool:
    """Swap the certificate without dropping a connection: select-config.sh, then ``nginx -s reload``."""
    docker = shutil.which("docker")
    if not docker:
        return False
    result = subprocess.run(  # noqa: S603
        [docker, "exec", PROXY_CONTAINER, "sh", "-c", RELOAD_COMMAND],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        why = (result.stderr or result.stdout).strip()[:200]
        warning(f"The proxy could not reload ({why}); restarting it.")
    return result.returncode == 0


def apply_to_proxy(install_dir: Path, *, recreate: bool = False, reload: bool = False) -> bool:
    """Have nginx pick up the install's material and ports; ``True`` once the console answers.

    A restart re-runs select-config.sh; new ports need the container made
    again (*recreate*), which ``docker compose up`` does when they changed.
    With HTTPS staying on and only the certificate new (*reload*: a renewal,
    a replacement), nginx reloads instead: new connections get the new
    certificate and the open ones -- the machines' streams -- stay up. A
    restart cut them, and the machines noticed only when their keepalive
    timed out, a minute later. Switching between HTTP and HTTPS restarts, so
    every open plain-HTTP connection meets the redirect at once.
    """
    from faden_cli.core.bootstrap import wait_for_backend  # noqa: PLC0415
    from faden_cli.core.compose import build_context_from_config, restart, up  # noqa: PLC0415

    if not _proxy_running():
        info("The proxy is not running; it serves this the next time the install starts.")
        return True
    ctx = build_context_from_config(load_config())
    if recreate:
        rc = up(ctx, services=[PROXY_SERVICE], detach=True, wait=True, timeout=120)
    elif reload and _reload_proxy():
        rc = 0
    else:
        rc = restart(ctx, services=[PROXY_SERVICE])
    if rc != 0:
        error(f"Restarting the proxy failed (exit code {rc}); see: faden logs {PROXY_SERVICE}")
        return False
    url = console_url(install_dir, read_env_file(install_dir / ".env"))
    if wait_for_backend(url, timeout=90):
        success(f"The console answers at {url}")
        return True
    error(f"The console does not answer at {url}; see: faden logs {PROXY_SERVICE}")
    return False


def addresses(install_dir: Path) -> list[str]:
    """The URLs other machines reach the console by, the way it is served now."""
    env = read_env_file(install_dir / ".env")
    https = tls.material_present(install_dir)
    loopback = {"localhost", "127.0.0.1", "::1"}
    return [url_for(name, https=https, env=env) for name in tls.machine_names() if name not in loopback]


def print_summary(summary: tls.CertSummary) -> None:
    """The certificate as an operator reads it, with what to watch out for."""
    console.print(f"  Certificate: [bold]{summary.subject}[/bold]")
    console.print(f"  Issued by:   {summary.issuer}{'  (this install)' if summary.made_here else ''}")
    console.print(f"  Valid until: {summary.not_after:%Y-%m-%d}")
    console.print(f"  Names:       {', '.join(summary.names) or '(none)'}")
    for note in summary.warnings:
        warning(note)


def reachable_example(urls: list[str]) -> str:
    """The address to show other machines: an IP or a dotted name before a bare host name.

    A bare name ("fadenstack") resolves on the server itself, rarely anywhere else.
    """
    for url in urls:
        host = urlsplit(url).hostname or ""
        if tls._is_ip(host) or "." in host:  # noqa: SLF001
            return url
    return urls[0] if urls else "https://<this server>"


def print_trust_hints(install_dir: Path, ca_public: Path) -> None:
    """How browsers, API clients and machines come to trust a certificate made here."""
    example = reachable_example(addresses(install_dir))
    console.print(
        "\n  [bold]Trust this install's certificate authority once[/bold],"
        " wherever the console is used from:\n"
        f"    {ca_public}\n"
        "  [dim]Browsers: import it as a trusted certificate authority (or accept the warning).\n"
        f"  API clients: curl --cacert ca.crt {example}/v1/models  ·  Python: SSL_CERT_FILE=ca.crt\n"
        "  Machines with agent 0.1.14 or later learnt it from this server and follow it to HTTPS\n"
        "  by themselves. Older agents stay on plain HTTP, which now only redirects: point each at\n"
        f"  {example} and give it the file (faden-agent configure --set BACKEND_URL=...\n"
        '  --set TLS_CA_BUNDLE=...; docs/installing.md, "Enabling HTTPS").[/dim]'
    )


def print_addresses(install_dir: Path) -> None:
    """Where other machines reach the console now."""
    urls = addresses(install_dir)
    if urls:
        console.print("\n  Reach the console at: " + "  ".join(urls))


TLS_MODES = ("off", "generate", "own")


def _free_port(label: str, port: int, *, yes: bool, current: tuple[int, int], other: int) -> int:
    """*port*, or another one the operator picks when something else holds it."""
    from faden_cli.commands.ports import port_conflicts  # noqa: PLC0415

    while True:
        pair = (port, other) if label == "HTTP" else (other, port)
        clash = [c for c in port_conflicts(*pair, current=current) if c.startswith(f"{label} port")]
        if not clash:
            return port
        if yes:
            error(f"{clash[0]} Free it, or pass --{label.lower()}-port.")
            sys.exit(1)
        warning(clash[0])
        port = click.prompt(f"  {label} port to use instead", type=click.IntRange(1, 65535))


def _ports_step(env_path: Path, *, yes: bool, http_port: int | None, https_port: int | None) -> None:
    from faden_cli.commands.ports import set_ports  # noqa: PLC0415
    from faden_cli.core.edge import check_ports, ports  # noqa: PLC0415

    current = ports(read_env_file(env_path))
    http = http_port if http_port is not None else current[0]
    https = https_port if https_port is not None else current[1]
    problem = check_ports(http, https)
    if problem:
        error(problem)
        sys.exit(2)
    http = _free_port("HTTP", http, yes=yes, current=current, other=https)
    https = _free_port("HTTPS", https, yes=yes, current=current, other=http)
    set_ports(env_path, http=http, https=https)
    console.print(
        f"  HTTP on port {http}, HTTPS on port {https} "
        "[dim](change: --http-port/--https-port, or later faden ports)[/dim]"
    )


def _ask_mode(install_dir: Path, *, yes: bool) -> str | None:
    """What to do about HTTPS when deploy was not told; ``None`` to leave it as it is."""
    present = tls.current(install_dir)
    if present is not None:
        info(
            f"HTTPS is on, with {present.subject} until {present.not_after:%Y-%m-%d}"
            " (change: faden tls)."
        )
        return None
    off_by_choice = tls.chose_off(install_dir)
    if yes:
        if off_by_choice:
            info(
                "HTTPS is off, as chosen. Switch it on any time: faden tls generate,"
                " or faden tls use CERT KEY.",
            )
            return None
        # HTTPS is the default: without it sign-ins, API keys, prompts and the
        # machines' credentials cross the network in the clear.
        info("HTTPS by default: a certificate made here (plain HTTP instead: faden deploy --tls off).")
        return "generate"
    console.print(
        "  Serve the console, the API and /v1 over HTTPS?\n"
        "    [bold]generate[/bold]  a certificate made here for this machine's names and addresses"
        " (default)\n"
        "    [bold]own[/bold]       your own certificate and key (PEM files)\n"
        "    [bold]off[/bold]       plain HTTP (switch HTTPS on later with faden tls)"
    )
    default = "off" if off_by_choice else "generate"
    return click.prompt("  HTTPS", type=click.Choice(TLS_MODES), default=default)


def renewal_scheduled() -> bool:
    """Whether ``faden tls renew`` runs by itself every day."""
    from faden_cli.core import schedule  # noqa: PLC0415

    try:
        return schedule.available() and schedule.job_current(schedule.TLS_UNIT) is not None
    except OSError:
        return False


def schedule_renewal(install_dir: Path) -> None:
    """Have ``faden tls renew`` run every morning, once HTTPS is on; say how."""
    from faden_cli.core import schedule  # noqa: PLC0415

    if renewal_scheduled():
        return
    if not schedule.available():
        warning(
            "No systemd user manager or cron here to renew the certificate by itself: "
            "run faden tls renew now and then (it only acts when something is due)."
        )
        return
    try:
        how, notes = schedule.install_job(schedule.tls_renewal(install_dir / "logs" / "tls-renew.log"))
    except (RuntimeError, OSError) as exc:
        warning(f"Could not schedule the renewal: {exc}. Run faden tls renew now and then.")
        return
    success(f"Kept current by itself: faden tls renew runs every morning ({how}).")
    for note in notes:
        warning(note)


def unschedule_renewal() -> None:
    """Stop the daily renewal: with HTTPS off there is nothing to renew."""
    from faden_cli.core import schedule  # noqa: PLC0415

    if schedule.available():
        schedule.remove_job(schedule.TLS_UNIT)


def refresh_edge_info(install_dir: Path) -> None:
    """Tell the backend this host's addresses and ports (``tls/edge.json``); best effort."""
    from faden_cli.core.edge import ports  # noqa: PLC0415

    http_port, https_port = ports(read_env_file(install_dir / ".env"))
    try:
        tls.write_edge_info(
            install_dir, http_port=http_port, https_port=https_port,
            renews="daily" if renewal_scheduled() else "by hand",
        )
    except OSError as exc:
        warning(f"Could not write {install_dir / tls.TLS_DIR / 'edge.json'}: {exc}")


def _wait_for_machines(trust_changed: bool, *, no_restart: bool, no_wait: bool) -> None:
    """Give connected machines time to fetch a new issuer before the proxy serves what it signed."""
    if not trust_changed or no_restart or no_wait:
        return
    info(
        f"The machines have a new issuer to trust: giving them {MACHINE_TRUST_WAIT_SEC} s to fetch it"
        " (--no-wait skips)."
    )
    time.sleep(MACHINE_TRUST_WAIT_SEC)


def _generate_step(install_dir: Path, *, yes: bool, names: tuple[str, ...]) -> Path:
    if not names and not yes:
        extra = click.prompt(
            "  Other names or addresses the console is reached by (comma-separated, blank for none)",
            default="",
            show_default=False,
        )
        names = tuple(n.strip() for n in extra.split(",") if n.strip())
    try:
        made = tls.generate(install_dir, names)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    success("HTTPS on, with a certificate made here.")
    print_summary(made.summary)
    return made.ca_public


def _own_step(
    install_dir: Path,
    *,
    yes: bool,
    cert: Path | None,
    key: Path | None,
    chain: Path | None,
    ca: Path | None,
) -> None:
    attempts = 1 if yes else 3
    for attempt in range(attempts):
        if cert is None or key is None:
            if yes:
                error("--tls own needs --tls-cert and --tls-key.")
                sys.exit(2)
            cert = Path(click.prompt("  Certificate file (PEM; may carry its chain)")).expanduser()
            key = Path(click.prompt("  Its private key (PEM, without a passphrase)")).expanduser()
            raw_chain = click.prompt(
                "  Intermediate certificates (PEM, blank for none)", default="", show_default=False
            )
            chain = Path(raw_chain).expanduser() if raw_chain.strip() else None
            raw_ca = click.prompt(
                "  Its issuer, when it is a private one (PEM, blank for a public issuer)",
                default="",
                show_default=False,
            )
            ca = Path(raw_ca).expanduser() if raw_ca.strip() else None
        try:
            summary = tls.use_own(install_dir, cert, key, chain, ca_path=ca)
        except tls.TlsError as exc:
            error(str(exc))
            if attempt + 1 == attempts:
                sys.exit(1)
            cert = key = chain = ca = None
            continue
        success("HTTPS on, with your certificate.")
        print_summary(summary)
        return


def deploy_step(
    install_dir: Path,
    env_path: Path,
    *,
    yes: bool,
    http_port: int | None,
    https_port: int | None,
    mode: str | None,
    cert: Path | None,
    key: Path | None,
    chain: Path | None,
    names: tuple[str, ...],
    ca: Path | None = None,
) -> Path | None:
    """``faden deploy``'s ports-and-HTTPS step, before anything starts.

    Returns the authority to trust when a certificate was made here. With
    ``--yes`` and no ``--tls``, what the install has stays as it is.
    """
    _ports_step(env_path, yes=yes, http_port=http_port, https_port=https_port)
    # Made even while HTTPS stays off: the machines fetch it from the backend
    # now, so they can follow once a certificate it signed is served.
    try:
        ca_public, changed = tls.ensure_ca(install_dir)
    except tls.TlsError as exc:
        warning(str(exc))
    else:
        if changed:
            info(f"Certificate authority for the machines: {ca_public}")

    result = _https_step(install_dir, yes=yes, mode=mode, cert=cert, key=key, chain=chain, names=names, ca=ca)
    if tls.material_present(install_dir):
        schedule_renewal(install_dir)
    refresh_edge_info(install_dir)
    return result


def _https_step(
    install_dir: Path,
    *,
    yes: bool,
    mode: str | None,
    cert: Path | None,
    key: Path | None,
    chain: Path | None,
    names: tuple[str, ...],
    ca: Path | None,
) -> Path | None:
    if mode is None and (cert or key):
        mode = "own"
    if mode is None:
        mode = _ask_mode(install_dir, yes=yes)
    if mode is None:
        return None
    if mode == "off":
        tls.remember_off(install_dir)
        if tls.remove(install_dir):
            unschedule_renewal()
            info("HTTPS switched off: the certificate was removed (the install's authority is kept).")
        else:
            info("HTTPS is off.")
        return None
    if mode == "generate":
        return _generate_step(install_dir, yes=yes, names=names)
    _own_step(install_dir, yes=yes, cert=cert, key=key, chain=chain, ca=ca)
    return None


@click.group("tls")
def tls_group() -> None:
    """HTTPS for the console, the API and the gateway (/v1).

    \b
    faden tls status                  what is served, until when, and how it is renewed
    faden tls generate                a certificate made here for this machine
    faden tls use CERT KEY            your own certificate (--chain, --ca for a private issuer)
    faden tls renew                   renew what is due (runs every morning by itself)
    faden tls off                     back to plain HTTP
    faden tls machine-ca              the machine CA behind model traffic TLS (--replace)
    """


def _renewal_line(install_dir: Path, summary: tls.CertSummary) -> str:
    left = (summary.not_after - datetime.now(UTC)).days
    auto = "every morning by faden tls renew" if renewal_scheduled() else "when you run faden tls renew"
    if summary.made_here:
        return f"issued again {tls.RENEW_BEFORE_DAYS} days before it runs out, {auto} ({left} days left)"
    origin = tls.source(install_dir)
    if origin is not None:
        return f"taken again from {origin['cert']} when it changes there, {auto} ({left} days left)"
    return f"by hand: faden tls use CERT KEY with the renewed files ({left} days left)"


@tls_group.command("status")
def status_cmd() -> None:
    """Show whether HTTPS is on, the certificate, until when, and how it is renewed."""
    install_dir = _install_dir()
    try:
        summary = tls.current(install_dir)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    if summary is None:
        console.print("HTTPS is [bold]off[/bold]: the console is served over plain HTTP.")
        console.print("[dim]  Switch it on: faden tls generate, or faden tls use CERT KEY[/dim]")
    else:
        console.print("HTTPS is [bold green]on[/bold green]; plain HTTP redirects to it.")
        print_summary(summary)
        console.print(f"  Renewed:     {_renewal_line(install_dir, summary)}")
        ca_public = install_dir / tls.TLS_DIR / tls.CA_PUBLIC_FILE
        if ca_public.is_file():
            console.print(f"  Machines trust: {ca_public}")
        if (summary.not_after - datetime.now(UTC)).days < tls.EXPIRY_WARNING_DAYS:
            warning(f"The certificate runs out on {summary.not_after:%Y-%m-%d}: faden tls renew")
    print_addresses(install_dir)


@tls_group.command("generate")
@click.option(
    "--name", "-n", "names", multiple=True, metavar="NAME",
    help="Another DNS name or address the console is reached by (repeatable). "
    "This machine's host names and addresses are always included.",
)
@click.option(
    "--no-restart", is_flag=True, help="Write the files only; the proxy picks them up when it next starts."
)
@click.option(
    "--no-wait", is_flag=True,
    help="Switch at once even when the authority is new (machines that have not fetched it stay behind).",
)
def generate_cmd(*, names: tuple[str, ...], no_restart: bool, no_wait: bool) -> None:
    """Make a certificate for this machine, signed by the install's own authority."""
    install_dir = _install_dir()
    was_on = tls.material_present(install_dir)
    try:
        made = tls.generate(install_dir, names)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    success("Certificate made and installed.")
    print_summary(made.summary)
    # Deploy and upgrade make the authority ahead of time; an install that has
    # not been through either since gets it only now.
    _wait_for_machines(made.trust_changed, no_restart=no_restart, no_wait=no_wait)
    if not no_restart and not apply_to_proxy(install_dir, reload=was_on):
        sys.exit(1)
    schedule_renewal(install_dir)
    refresh_edge_info(install_dir)
    print_trust_hints(install_dir, made.ca_public)
    print_addresses(install_dir)


@tls_group.command("use")
@click.argument("cert", type=click.Path(dir_okay=False, path_type=Path))
@click.argument("key", type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--chain", type=click.Path(dir_okay=False, path_type=Path), default=None,
    help="The issuer's intermediate certificates (PEM), when CERT does not already carry them.",
)
@click.option(
    "--ca", "ca", type=click.Path(dir_okay=False, path_type=Path), default=None,
    help="A private issuer's root (PEM): the machines are given it, so they trust the certificate. "
    "Not needed for a public issuer, nor when CERT's chain ends in the root.",
)
@click.option(
    "--no-restart", is_flag=True, help="Write the files only; the proxy picks them up when it next starts."
)
@click.option(
    "--no-wait", is_flag=True,
    help="Switch at once even when the issuer is new to the machines.",
)
def use_cmd(
    *, cert: Path, key: Path, chain: Path | None, ca: Path | None, no_restart: bool, no_wait: bool
) -> None:
    """Serve your own certificate: CERT and KEY are PEM files (CERT may be a full chain).

    The files are read again from the same place by faden tls renew when they
    change there, so a certificate renewed in place (certbot, acme.sh, your own
    tooling) is served without anyone running this again.
    """
    install_dir = _install_dir()
    was_on = tls.material_present(install_dir)
    try:
        summary = tls.use_own(install_dir, cert, key, chain, ca_path=ca)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    success("Certificate checked and installed.")
    print_summary(summary)
    _wait_for_machines(summary.trust_changed, no_restart=no_restart, no_wait=no_wait)
    if not no_restart and not apply_to_proxy(install_dir, reload=was_on):
        sys.exit(1)
    schedule_renewal(install_dir)
    refresh_edge_info(install_dir)
    print_addresses(install_dir)


@tls_group.command("renew")
@click.option(
    "--days", type=click.IntRange(1, tls.CA_DAYS), default=tls.RENEW_BEFORE_DAYS, show_default=True,
    help="Issue a certificate made here again once it has fewer days left than this.",
)
@click.option("--force", is_flag=True, help="Issue it again, or take the operator's files again, now.")
@click.option("--no-wait", is_flag=True, help="Do not wait for the machines when their issuer changed.")
def renew_cmd(*, days: int, force: bool, no_wait: bool) -> None:
    """Renew what is due; changes nothing otherwise. Runs every morning by itself once HTTPS is on.

    Exits 1 when a certificate runs out soon and cannot be renewed from here,
    so a failed run shows (systemctl --user --failed).
    """
    install_dir = _install_dir()
    # A rotation of the authority is published first, so machines have it
    # before anything it signed is served.
    try:
        _, rotated = tls.ensure_ca(install_dir)
    except tls.TlsError as exc:
        warning(str(exc))
        rotated = False
    try:
        result = tls.renew(install_dir, days=days, force=force)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if result.action in ("renewed", "reimported"):
        success(f"{stamp}  {result.message}")
        if result.summary is not None:
            print_summary(result.summary)
        _wait_for_machines(result.trust_changed or rotated, no_restart=False, no_wait=no_wait)
        if not apply_to_proxy(install_dir, reload=True):
            sys.exit(1)
        refresh_edge_info(install_dir)
        return
    if result.action == "expiring":
        warning(f"{stamp}  {result.message}")
        sys.exit(1)
    info(f"{stamp}  {result.message}")


@tls_group.command("off")
@click.option(
    "--no-restart", is_flag=True, help="Remove the files only; the proxy follows when it next starts."
)
def off_cmd(*, no_restart: bool) -> None:
    """Go back to plain HTTP. The install's authority is kept for next time."""
    install_dir = _install_dir()
    tls.remember_off(install_dir)
    if not tls.remove(install_dir):
        info("HTTPS was already off.")
        return
    success("Certificate removed.")
    unschedule_renewal()
    if not no_restart and not apply_to_proxy(install_dir):
        sys.exit(1)
    refresh_edge_info(install_dir)
    urls = addresses(install_dir)
    warning(
        "Machines that followed this server to HTTPS stay there: an agent never falls back to plain HTTP "
        "by itself. Point each back with: faden-agent configure --set BACKEND_URL="
        f"{reachable_example(urls) if urls else 'http://<this server>'}, then restart it."
    )
    env = read_env_file(install_dir / ".env")
    if (env.get("FADEN_HSTS_MAX_AGE") or "0").strip() not in ("", "0"):
        warning(
            "FADEN_HSTS_MAX_AGE is set: browsers that saw it keep insisting on HTTPS until it runs out."
        )
    print_addresses(install_dir)


#: Run in the backend container: the machine CA lives in its database, sealed with its master key.
_MACHINE_CA_SCRIPT = """
import asyncio, json, sys
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from faden_backend.settings import settings
from faden_backend.services.machine_tls import store

async def main(replace):
    engine = create_async_engine(str(settings.db_url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if replace:
                await store.rotate(session, settings.settings_master_key)
                await session.commit()
            print(json.dumps({"enabled": settings.machine_tls_enabled, **(await store.status(session))}))
    finally:
        await engine.dispose()

asyncio.run(main(sys.argv[1] == "replace"))
"""


def _machine_ca(replace: bool) -> dict:
    import json  # noqa: PLC0415

    docker = shutil.which("docker")
    if not docker:
        error("docker not found on PATH")
        sys.exit(1)
    proc = subprocess.run(  # noqa: S603
        [docker, "exec", "-i", "faden-backend", "python", "-", "replace" if replace else "status"],
        input=_MACHINE_CA_SCRIPT, capture_output=True, text=True, check=False,
    )
    line = next((ln for ln in reversed(proc.stdout.strip().splitlines()) if ln.startswith("{")), "")
    if proc.returncode != 0 or not line:
        error("Could not read the machine CA from the backend -- is faden-backend running?")
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        if detail:
            error(f"  {detail[-1]}")
        sys.exit(1)
    return json.loads(line)


@tls_group.command("machine-ca")
@click.option(
    "--replace", is_flag=True, default=False,
    help="Make a new machine CA now and distrust every earlier one (a suspected leak).",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask before --replace.")
def machine_ca_cmd(*, replace: bool, yes: bool) -> None:
    """The machine CA: the TLS between the gateway and the models on the machines.

    It looks after itself -- renewals, the yearly rotation, the gateway's
    certificate -- in the backend. This shows it; --replace is for a suspected
    leak: the gateway and every machine move to a new CA within a minute, and
    model calls fail until they have.
    """
    if replace and not yes:
        click.confirm(
            "Replace the machine CA now? Model calls fail for about a minute while the gateway"
            " and the machines take new certificates.",
            abort=True,
        )
    doc = _machine_ca(replace)
    if not doc.get("enabled"):
        console.print(
            "Model traffic TLS is [bold]switched off[/bold] (FADEN_BACKEND_MACHINE_TLS_ENABLED=false).",
        )
        return
    if replace:
        success("Machine CA replaced; the gateway and the machines move to it within a minute.")
    now = datetime.now(UTC)
    for ca in doc.get("authorities") or []:
        until = datetime.fromisoformat(ca["not_after"])
        role = "signs now" if ca.get("issuing") else f"signs from {ca['issuing_from'][:10]}"
        console.print(
            f"  Machine CA  {ca['fingerprint_sha256'][:16]}…  until {until:%Y-%m-%d}"
            f" ({(until - now).days} days), {role} [dim]({ca.get('reason')})[/dim]"
        )
    gateway = doc.get("gateway")
    if gateway:
        until = datetime.fromisoformat(gateway["not_after"])
        state = ""
        if not gateway.get("current"):
            state = " [yellow](from an earlier CA; renewed within the hour)[/yellow]"
        console.print(f"  Gateway     until {until:%Y-%m-%d} ({(until - now).days} days){state}")
    machines = doc.get("machines") or {}
    for entry in sorted(machines.values(), key=lambda m: ",".join(m.get("names") or [])):
        until = datetime.fromisoformat(entry["not_after"])
        state = ""
        if not entry.get("current"):
            state = " [yellow](from an earlier CA; renews within a minute)[/yellow]"
        console.print(
            f"  Machine     {', '.join(entry.get('names') or ['?']):<16} until {until:%Y-%m-%d}"
            f" ({(until - now).days} days){state}"
        )
    if not machines:
        console.print("  [dim]No machine has asked for a certificate yet (agents 0.1.16 and later do).[/dim]")

